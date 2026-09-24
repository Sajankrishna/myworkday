using System.Net;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json.Nodes;

namespace MyWorkDay.Core;

/// <summary>Real Google Calendar OAuth 2.0 "installed app" loopback flow: open the browser for
/// consent, catch the redirect on a one-shot local HTTP listener, exchange the code for a
/// refresh token, store only that (never the short-lived access token), mint a fresh access
/// token from it on every fetch. Ported 1:1 from the Python build's start_google_oauth /
/// google_access_token / fetch_today_calendar.</summary>
public sealed class GoogleCalendarService
{
    private const string AuthUrl = "https://accounts.google.com/o/oauth2/v2/auth";
    private const string TokenUrl = "https://oauth2.googleapis.com/token";
    private const string Scope = "https://www.googleapis.com/auth/calendar.readonly";

    // A pre-registered OAuth client so people using this app can click "Connect Google" without
    // first creating their own Google Cloud project - Google's own docs note this "installed
    // app" client type's secret isn't meaningfully confidential, since it ships inside every
    // copy of the app regardless. Anyone can paste their own Client ID/Secret in Settings instead.
    public const string SharedClientId = "793559634176-h8hlp7llp1r95f1pomk858mce2947kut.apps.googleusercontent.com";
    public const string SharedClientSecret = "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";

    private readonly HttpClient _http = new();
    private readonly ErrorLogService _errorLog;

    public GoogleCalendarService(ErrorLogService errorLog) => _errorLog = errorLog;

    public async Task<(bool Ok, string? RefreshToken, string? Error)> ConnectAsync(string clientId, string clientSecret)
    {
        string? code = null;
        string? error = null;
        HttpListener? listener = null;
        string redirectUri;
        try
        {
            listener = new HttpListener();
            // Bind port 0 isn't supported by HttpListener directly - probe a free loopback port
            // first, then listen on that exact prefix.
            var port = GetFreeLoopbackPort();
            redirectUri = $"http://127.0.0.1:{port}/";
            listener.Prefixes.Add(redirectUri);
            listener.Start();

            var authUrl = $"{AuthUrl}?client_id={Uri.EscapeDataString(clientId)}" +
                           $"&redirect_uri={Uri.EscapeDataString(redirectUri)}" +
                           "&response_type=code" +
                           $"&scope={Uri.EscapeDataString(Scope)}" +
                           "&access_type=offline&prompt=consent";
            OpenBrowser(authUrl);

            var getContextTask = listener.GetContextAsync();
            var completed = await Task.WhenAny(getContextTask, Task.Delay(TimeSpan.FromMinutes(2)));
            if (completed != getContextTask)
                return (false, null, "Timed out waiting for Google sign-in (2 min).");

            var ctx = getContextTask.Result;
            var query = ctx.Request.QueryString;
            code = query["code"];
            error = query["error"];

            var html = code != null
                ? "<html><body><h3>Connected - you can close this tab.</h3></body></html>"
                : "<html><body><h3>Google sign-in was cancelled or failed.</h3></body></html>";
            var buffer = System.Text.Encoding.UTF8.GetBytes(html);
            ctx.Response.ContentType = "text/html";
            await ctx.Response.OutputStream.WriteAsync(buffer);
            ctx.Response.Close();
        }
        catch (Exception ex)
        {
            _errorLog.Log("Google OAuth loopback failed", ex);
            return (false, null, $"Local sign-in listener failed: {ex.Message}");
        }
        finally
        {
            listener?.Stop();
            listener?.Close();
        }

        if (error != null) return (false, null, $"Google sign-in error: {error}");
        if (code is null) return (false, null, "Timed out waiting for Google sign-in (2 min).");

        try
        {
            var form = new FormUrlEncodedContent(new Dictionary<string, string>
            {
                ["client_id"] = clientId,
                ["client_secret"] = clientSecret,
                ["code"] = code,
                ["grant_type"] = "authorization_code",
                ["redirect_uri"] = redirectUri,
            });
            using var resp = await _http.PostAsync(TokenUrl, form);
            var json = await resp.Content.ReadFromJsonAsync<JsonNode>();
            var refreshToken = json?["refresh_token"]?.GetValue<string>();
            if (refreshToken is null)
                return (false, null, "Google didn't return a refresh token - revoke this app's access at myaccount.google.com/permissions and try again.");
            return (true, refreshToken, null);
        }
        catch (Exception ex)
        {
            return (false, null, $"Token exchange failed: {ex.Message}");
        }
    }

    private static int GetFreeLoopbackPort()
    {
        var listener = new System.Net.Sockets.TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var port = ((IPEndPoint)listener.LocalEndpoint).Port;
        listener.Stop();
        return port;
    }

    private static void OpenBrowser(string url)
    {
        try
        {
            System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(url) { UseShellExecute = true });
        }
        catch { }
    }

    private async Task<string?> GetAccessTokenAsync(string clientId, string clientSecret, string refreshToken)
    {
        var form = new FormUrlEncodedContent(new Dictionary<string, string>
        {
            ["client_id"] = clientId,
            ["client_secret"] = clientSecret,
            ["refresh_token"] = refreshToken,
            ["grant_type"] = "refresh_token",
        });
        using var resp = await _http.PostAsync(TokenUrl, form);
        if (!resp.IsSuccessStatusCode) return null;
        var json = await resp.Content.ReadFromJsonAsync<JsonNode>();
        return json?["access_token"]?.GetValue<string>();
    }

    public async Task<CalendarFetchResult?> FetchTodayAsync(string clientId, string clientSecret, string refreshToken)
    {
        try
        {
            var token = await GetAccessTokenAsync(clientId, clientSecret, refreshToken);
            if (token is null) return null;

            var now = TimeZoneInfo.ConvertTime(DateTimeOffset.Now, WorkDate.Eastern);
            var start = new DateTimeOffset(now.Year, now.Month, now.Day, 0, 0, 0, now.Offset);
            var end = start.AddDays(1);
            var url = "https://www.googleapis.com/calendar/v3/calendars/primary/events" +
                      $"?timeMin={Uri.EscapeDataString(start.ToString("o"))}" +
                      $"&timeMax={Uri.EscapeDataString(end.ToString("o"))}" +
                      "&singleEvents=true&orderBy=startTime&maxResults=20";
            using var req = new HttpRequestMessage(HttpMethod.Get, url);
            req.Headers.Authorization = new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", token);
            using var resp = await _http.SendAsync(req);
            if (!resp.IsSuccessStatusCode) return null;
            var json = await resp.Content.ReadFromJsonAsync<JsonNode>();

            var events = new List<CalendarEventRaw>();
            foreach (var e in json?["items"]?.AsArray() ?? new JsonArray())
            {
                if (e is null) continue;
                var startVal = e["start"]?["dateTime"]?.GetValue<string>();
                if (startVal is null) continue; // skip all-day events - not real meetings
                var endVal = e["end"]?["dateTime"]?.GetValue<string>();
                events.Add(new CalendarEventRaw
                {
                    Title = Truncate(e["summary"]?.GetValue<string>() ?? "(no title)", 60),
                    Start = DateTimeOffset.Parse(startVal),
                    End = endVal != null ? DateTimeOffset.Parse(endVal) : null,
                    Source = EventSource(e),
                });
            }
            events.Sort((a, b) => a.Start.CompareTo(b.Start));
            return new CalendarFetchResult { Count = events.Count, Events = events };
        }
        catch (Exception ex)
        {
            _errorLog.Log("FetchTodayAsync (calendar) failed", ex);
            return null;
        }
    }

    private static string? EventSource(JsonNode e)
    {
        var confName = e["conferenceData"]?["conferenceSolution"]?["name"]?.GetValue<string>();
        if (!string.IsNullOrWhiteSpace(confName)) return confName;
        if (e["hangoutLink"] != null) return "Google Meet";
        var loc = e["location"]?.GetValue<string>()?.Trim();
        return string.IsNullOrEmpty(loc) ? null : Truncate(loc, 30);
    }

    private static string Truncate(string s, int len) => s.Length <= len ? s : s[..len];
}
