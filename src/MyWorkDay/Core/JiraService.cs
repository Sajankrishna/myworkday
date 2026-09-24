using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace MyWorkDay.Core;

/// <summary>All real Jira Cloud REST calls this app makes. Jira-only by design (no git
/// scanning in this build) - every figure here traces back to a real worklog entry, a real
/// comment, or a real assignment, fetched fresh on each call.</summary>
public sealed class JiraService
{
    private readonly ConfigService _config;
    private readonly ErrorLogService _errorLog;
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(20) };

    public JiraService(ConfigService config, ErrorLogService errorLog)
    {
        _config = config;
        _errorLog = errorLog;
    }

    private (string? Base, string? Email, string? Token) Creds()
    {
        var cfg = _config.Load();
        return (cfg.JiraBaseUrl?.TrimEnd('/'), cfg.JiraEmail, cfg.JiraApiToken);
    }

    private HttpRequestMessage NewRequest(HttpMethod method, string url, string email, string token)
    {
        var req = new HttpRequestMessage(method, url);
        var auth = Convert.ToBase64String(Encoding.UTF8.GetBytes($"{email}:{token}"));
        req.Headers.Authorization = new AuthenticationHeaderValue("Basic", auth);
        return req;
    }

    public async Task<JiraTestResult> TestConnectionAsync(string baseUrl, string email, string token)
    {
        if (string.IsNullOrWhiteSpace(baseUrl) || string.IsNullOrWhiteSpace(email) || string.IsNullOrWhiteSpace(token))
            return new JiraTestResult { Ok = false, Error = "Base URL, email and API token are all required." };
        try
        {
            var url = baseUrl.TrimEnd('/') + "/rest/api/2/myself";
            using var req = NewRequest(HttpMethod.Get, url, email, token);
            using var resp = await _http.SendAsync(req);
            if (resp.StatusCode == System.Net.HttpStatusCode.Unauthorized)
                return new JiraTestResult { Ok = false, Error = "Jira rejected those credentials (401 Unauthorized)." };
            if (!resp.IsSuccessStatusCode)
                return new JiraTestResult { Ok = false, Error = $"Jira returned HTTP {(int)resp.StatusCode}." };
            var json = await resp.Content.ReadFromJsonAsync<JsonNode>();
            var name = json?["displayName"]?.GetValue<string>() ?? email;
            return new JiraTestResult { Ok = true, DisplayName = name };
        }
        catch (Exception e)
        {
            return new JiraTestResult { Ok = false, Error = $"Could not reach {baseUrl}: {e.Message}" };
        }
    }

    public async Task<string?> GetDisplayNameAsync()
    {
        var (b, e, t) = Creds();
        if (b is null || e is null || t is null) return null;
        try
        {
            using var req = NewRequest(HttpMethod.Get, b + "/rest/api/2/myself", e, t);
            using var resp = await _http.SendAsync(req);
            if (!resp.IsSuccessStatusCode) return null;
            var json = await resp.Content.ReadFromJsonAsync<JsonNode>();
            return json?["displayName"]?.GetValue<string>();
        }
        catch { return null; }
    }

    public async Task<string?> GetMyAccountIdAsync()
    {
        var (b, e, t) = Creds();
        if (b is null || e is null || t is null) return null;
        try
        {
            using var req = NewRequest(HttpMethod.Get, b + "/rest/api/3/myself", e, t);
            using var resp = await _http.SendAsync(req);
            if (!resp.IsSuccessStatusCode) return null;
            var json = await resp.Content.ReadFromJsonAsync<JsonNode>();
            return json?["accountId"]?.GetValue<string>();
        }
        catch { return null; }
    }

    /// <summary>Statuses for a batch of ticket keys - /rest/api/2/search was retired (410
    /// Gone); this POSTs the current /rest/api/3/search/jql replacement.</summary>
    public async Task<Dictionary<string, JiraIssueStatus>> GetStatusesAsync(IEnumerable<string> keys)
    {
        var (b, e, t) = Creds();
        var keyList = keys.Distinct().OrderBy(k => k).ToList();
        var result = new Dictionary<string, JiraIssueStatus>();
        if (b is null || e is null || t is null || keyList.Count == 0) return result;
        try
        {
            var jql = "key in (" + string.Join(",", keyList) + ")";
            var body = new JsonObject
            {
                ["jql"] = jql,
                ["fields"] = new JsonArray("status", "assignee"),
                ["maxResults"] = 200,
            };
            using var req = NewRequest(HttpMethod.Post, b + "/rest/api/3/search/jql", e, t);
            req.Content = JsonContent.Create(body);
            using var resp = await _http.SendAsync(req);
            if (!resp.IsSuccessStatusCode) return result;
            var json = await resp.Content.ReadFromJsonAsync<JsonNode>();
            foreach (var issue in json?["issues"]?.AsArray() ?? new JsonArray())
            {
                if (issue is null) continue;
                var key = issue["key"]!.GetValue<string>();
                var fields = issue["fields"];
                var status = fields?["status"];
                var category = status?["statusCategory"];
                var assignee = fields?["assignee"];
                result[key] = new JiraIssueStatus
                {
                    Status = status?["name"]?.GetValue<string>(),
                    StatusCategory = category?["key"]?.GetValue<string>(),
                    AssigneeEmail = assignee?["emailAddress"]?.GetValue<string>(),
                };
            }
        }
        catch (Exception ex)
        {
            _errorLog.Log("GetStatusesAsync failed", ex);
        }
        return result;
    }

    /// <summary>Tickets you actually left a real Jira worklog entry on, dated to the given
    /// work-day (Eastern, 10pm-cutoff bucketing). Two-phase, same pattern as team worklogs:
    /// JQL narrows to candidate issues via worklogAuthor/worklogDate (widened a day either
    /// side to survive the cutoff shift), then each candidate's own worklog list is fetched
    /// and precisely re-bucketed by real timestamp.</summary>
    public async Task<Dictionary<string, TicketDayRow>> GetMyWorklogRowsAsync(string dateKey)
    {
        var (b, e, t) = Creds();
        var rows = new Dictionary<string, TicketDayRow>();
        if (b is null || e is null || t is null) return rows;
        var accountId = await GetMyAccountIdAsync();
        if (accountId is null) return rows;

        var day = WorkDate.Parse(dateKey);
        var lo = day.AddDays(-1).ToString("yyyy-MM-dd");
        var hi = day.AddDays(1).ToString("yyyy-MM-dd");
        var jql = $"worklogAuthor = \"{accountId}\" AND worklogDate >= \"{lo}\" AND worklogDate <= \"{hi}\"";

        List<(string Key, string Summary, string? Status, string? Category, int? ExpectedMinutes)> candidates;
        try
        {
            candidates = await SearchIssuesAsync(b, e, t, jql, new[] { "summary", "status", "timetracking" });
        }
        catch (Exception ex)
        {
            _errorLog.Log("GetMyWorklogRowsAsync search failed", ex);
            return rows;
        }

        var tasks = candidates.Select(async c =>
        {
            var entries = await FetchWorklogEntriesAsync(b, e, t, c.Key, accountId, dateKey);
            return (c, entries);
        });
        var results = await Task.WhenAll(tasks);

        foreach (var (c, entries) in results)
        {
            if (entries.Count == 0) continue;
            var total = entries.Sum(x => x.Minutes);
            rows[c.Key] = new TicketDayRow
            {
                Key = c.Key,
                Summary = c.Summary,
                Status = c.Status,
                StatusCategory = c.Category,
                LoggedMinutes = total,
                ExpectedMinutes = c.ExpectedMinutes,
                JiraUrl = b + "/browse/" + c.Key,
                Actions = entries.Select(x => new TicketAction
                {
                    Timestamp = x.Started,
                    Kind = "worklog",
                    Message = string.IsNullOrWhiteSpace(x.Comment) ? "Logged time" : x.Comment,
                    Minutes = x.Minutes,
                }).OrderBy(a => a.Timestamp).ToList(),
            };
        }
        return rows;
    }

    private async Task<List<(DateTimeOffset Started, int Minutes, string? Comment)>> FetchWorklogEntriesAsync(
        string baseUrl, string email, string token, string key, string accountId, string dateKey)
    {
        var found = new List<(DateTimeOffset, int, string?)>();
        var startAt = 0;
        try
        {
            while (true)
            {
                var url = $"{baseUrl}/rest/api/3/issue/{key}/worklog?startAt={startAt}&maxResults=100";
                using var req = NewRequest(HttpMethod.Get, url, email, token);
                using var resp = await _http.SendAsync(req);
                if (!resp.IsSuccessStatusCode) break;
                var json = await resp.Content.ReadFromJsonAsync<JsonNode>();
                var worklogs = json?["worklogs"]?.AsArray() ?? new JsonArray();
                foreach (var w in worklogs)
                {
                    if (w is null) continue;
                    var authorId = w["author"]?["accountId"]?.GetValue<string>();
                    if (authorId != accountId) continue;
                    var startedStr = w["started"]?.GetValue<string>();
                    var secs = w["timeSpentSeconds"]?.GetValue<int>() ?? 0;
                    if (startedStr is null || secs == 0) continue;
                    var started = DateTimeOffset.Parse(startedStr);
                    if (WorkDate.KeyFor(started) != dateKey) continue;
                    var comment = ExtractCommentText(w["comment"]);
                    found.Add((started, secs / 60, comment));
                }
                startAt += worklogs.Count;
                var total = json?["total"]?.GetValue<int>() ?? 0;
                if (startAt >= total || worklogs.Count == 0) break;
            }
        }
        catch
        {
            // one ticket's worklog page failing shouldn't drop everything else
        }
        return found;
    }

    private static string? ExtractCommentText(JsonNode? comment)
    {
        // Jira's worklog comment is Atlassian Document Format (nested content nodes), not a
        // plain string - walk it for the first real text run, else fall back silently.
        if (comment is null) return null;
        if (comment is JsonValue v && v.TryGetValue<string>(out var s)) return s;
        try
        {
            var texts = new List<string>();
            void Walk(JsonNode? node)
            {
                if (node is JsonObject obj)
                {
                    if (obj["type"]?.GetValue<string>() == "text" && obj["text"] is JsonValue tv && tv.TryGetValue<string>(out var tx))
                        texts.Add(tx);
                    foreach (var kv in obj)
                        if (kv.Key == "content") Walk(kv.Value);
                }
                else if (node is JsonArray arr)
                {
                    foreach (var item in arr) Walk(item);
                }
            }
            Walk(comment);
            return texts.Count > 0 ? string.Join(" ", texts) : null;
        }
        catch { return null; }
    }

    /// <summary>Tickets you left a real comment on, within the lookback window - the one
    /// activity signal a worklog-only view can never see. Bounded to your own issues (assignee
    /// or reporter = you), not an instance-wide scan. Returns full rows (summary/status
    /// included) so the caller doesn't need a second status lookup for comment-only tickets.</summary>
    public async Task<Dictionary<string, TicketDayRow>> GetCommentedRowsAsync(string accountId, int lookbackDays)
    {
        var (b, e, t) = Creds();
        var result = new Dictionary<string, TicketDayRow>();
        if (b is null || e is null || t is null) return result;

        var since = DateTimeOffset.Now.AddDays(-lookbackDays);
        var jql = $"(assignee = \"{accountId}\" OR reporter = \"{accountId}\") AND updated >= \"{since:yyyy/MM/dd HH:mm}\"";
        List<(string Key, string Summary, string? Status, string? Category, int? ExpectedMinutes)> issues;
        try
        {
            issues = await SearchIssuesAsync(b, e, t, jql, new[] { "summary", "status", "timetracking" });
        }
        catch (Exception ex)
        {
            _errorLog.Log("GetCommentedRowsAsync search failed", ex);
            return result;
        }
        if (issues.Count == 0) return result;

        var tasks = issues.Select(async issue =>
        {
            var comments = await FetchMyCommentsAsync(b, e, t, issue.Key, accountId, since);
            return (issue, comments);
        });
        foreach (var (issue, comments) in await Task.WhenAll(tasks))
        {
            if (comments.Count == 0) continue;
            result[issue.Key] = new TicketDayRow
            {
                Key = issue.Key,
                Summary = issue.Summary,
                Status = issue.Status,
                StatusCategory = issue.Category,
                LoggedMinutes = 0,
                ExpectedMinutes = issue.ExpectedMinutes,
                JiraUrl = b + "/browse/" + issue.Key,
                Actions = comments,
            };
        }
        return result;
    }

    private async Task<List<TicketAction>> FetchMyCommentsAsync(
        string baseUrl, string email, string token, string key, string accountId, DateTimeOffset since)
    {
        var rows = new List<TicketAction>();
        try
        {
            var url = $"{baseUrl}/rest/api/3/issue/{key}/comment?maxResults=100&orderBy=-created";
            using var req = NewRequest(HttpMethod.Get, url, email, token);
            using var resp = await _http.SendAsync(req);
            if (!resp.IsSuccessStatusCode) return rows;
            var json = await resp.Content.ReadFromJsonAsync<JsonNode>();
            foreach (var c in json?["comments"]?.AsArray() ?? new JsonArray())
            {
                if (c is null) continue;
                var createdStr = c["created"]?.GetValue<string>();
                if (createdStr is null) continue;
                var created = DateTimeOffset.Parse(createdStr);
                // Newest-first - once one comment is older than the window, every comment
                // after it is guaranteed older too.
                if (created < since) break;
                var authorId = c["author"]?["accountId"]?.GetValue<string>();
                if (authorId != accountId) continue;
                var body = ExtractCommentText(c["body"]) ?? "(comment)";
                rows.Add(new TicketAction { Timestamp = created, Kind = "comment", Message = body, Minutes = 0 });
            }
        }
        catch
        {
            // one ticket's comment page failing shouldn't drop everything else
        }
        return rows;
    }

    /// <summary>Tickets currently assigned to you that aren't Done - your real open workload,
    /// independent of any single day's activity.</summary>
    public async Task<List<OpenTicketRow>> GetMyOpenAssignedAsync()
    {
        var (b, e, t) = Creds();
        var rows = new List<OpenTicketRow>();
        if (b is null || e is null || t is null) return rows;
        try
        {
            var jql = "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC";
            var issues = await SearchIssuesAsync(b, e, t, jql, new[] { "summary", "status", "timetracking" });
            foreach (var i in issues)
                rows.Add(new OpenTicketRow
                {
                    Key = i.Key,
                    Summary = i.Summary,
                    Status = i.Status,
                    StatusCategory = i.Category,
                    ExpectedMinutes = i.ExpectedMinutes,
                    JiraUrl = b + "/browse/" + i.Key,
                });
        }
        catch (Exception ex)
        {
            _errorLog.Log("GetMyOpenAssignedAsync failed", ex);
        }
        return rows;
    }

    public async Task<List<JiraUserSearchResult>> SearchUsersAsync(string query)
    {
        var (b, e, t) = Creds();
        var results = new List<JiraUserSearchResult>();
        if (b is null || e is null || t is null || string.IsNullOrWhiteSpace(query)) return results;
        try
        {
            var url = $"{b}/rest/api/3/user/search?query={Uri.EscapeDataString(query.Trim())}&maxResults=15";
            using var req = NewRequest(HttpMethod.Get, url, e, t);
            using var resp = await _http.SendAsync(req);
            if (!resp.IsSuccessStatusCode) return results;
            var json = await resp.Content.ReadFromJsonAsync<JsonArray>();
            foreach (var u in json ?? new JsonArray())
            {
                if (u is null) continue;
                if (u["accountType"]?.GetValue<string>() != "atlassian") continue;
                results.Add(new JiraUserSearchResult
                {
                    AccountId = u["accountId"]?.GetValue<string>() ?? "",
                    DisplayName = u["displayName"]?.GetValue<string>() ?? "",
                    Email = u["emailAddress"]?.GetValue<string>() ?? "",
                    AvatarUrl = u["avatarUrls"]?["24x24"]?.GetValue<string>(),
                });
            }
        }
        catch (Exception ex)
        {
            _errorLog.Log("SearchUsersAsync failed", ex);
        }
        return results;
    }

    /// <summary>Per-ticket worklog breakdown for one team member on one work-date. Jira has no
    /// "everything this account logged today" endpoint, so this finds candidates via
    /// worklogAuthor/worklogDate JQL (widened a day either side for the cutoff shift), then
    /// fetches and precisely re-buckets each candidate's own worklog list.</summary>
    public async Task<(List<TeamMemberTicket> Tickets, int TotalMinutes)> GetTeamMemberTicketsAsync(string accountId, string dateKey)
    {
        var (b, e, t) = Creds();
        if (b is null || e is null || t is null) return (new(), 0);

        var day = WorkDate.Parse(dateKey);
        var lo = day.AddDays(-1).ToString("yyyy-MM-dd");
        var hi = day.AddDays(1).ToString("yyyy-MM-dd");
        var jql = $"worklogAuthor = \"{accountId}\" AND worklogDate >= \"{lo}\" AND worklogDate <= \"{hi}\"";
        List<(string Key, string Summary, string? Status, string? Category, int? ExpectedMinutes)> issues;
        try
        {
            issues = await SearchIssuesAsync(b, e, t, jql, new[] { "summary", "status", "timetracking" });
        }
        catch
        {
            return (new(), 0);
        }
        if (issues.Count == 0) return (new(), 0);

        var tasks = issues.Select(async i =>
        {
            var mins = await SumWorklogMinutesForAccountAsync(b, e, t, i.Key, accountId, dateKey);
            return (i, mins);
        });
        var tickets = new List<TeamMemberTicket>();
        foreach (var (i, mins) in await Task.WhenAll(tasks))
        {
            if (mins <= 0) continue;
            tickets.Add(new TeamMemberTicket { Key = i.Key, Summary = i.Summary, Status = i.Status, StatusCategory = i.Category, Minutes = mins });
        }
        tickets = tickets.OrderByDescending(x => x.Minutes).ToList();
        return (tickets, tickets.Sum(x => x.Minutes));
    }

    private async Task<int> SumWorklogMinutesForAccountAsync(string baseUrl, string email, string token, string key, string accountId, string dateKey)
    {
        var mins = 0;
        var startAt = 0;
        try
        {
            while (true)
            {
                var url = $"{baseUrl}/rest/api/3/issue/{key}/worklog?startAt={startAt}&maxResults=100";
                using var req = NewRequest(HttpMethod.Get, url, email, token);
                using var resp = await _http.SendAsync(req);
                if (!resp.IsSuccessStatusCode) break;
                var json = await resp.Content.ReadFromJsonAsync<JsonNode>();
                var worklogs = json?["worklogs"]?.AsArray() ?? new JsonArray();
                foreach (var w in worklogs)
                {
                    if (w is null) continue;
                    if (w["author"]?["accountId"]?.GetValue<string>() != accountId) continue;
                    var startedStr = w["started"]?.GetValue<string>();
                    var secs = w["timeSpentSeconds"]?.GetValue<int>() ?? 0;
                    if (startedStr is null || secs == 0) continue;
                    if (WorkDate.KeyFor(DateTimeOffset.Parse(startedStr)) == dateKey) mins += secs / 60;
                }
                startAt += worklogs.Count;
                var total = json?["total"]?.GetValue<int>() ?? 0;
                if (startAt >= total || worklogs.Count == 0) break;
            }
        }
        catch { }
        return mins;
    }

    private async Task<List<(string Key, string Summary, string? Status, string? Category, int? ExpectedMinutes)>> SearchIssuesAsync(
        string baseUrl, string email, string token, string jql, string[] fields)
    {
        var body = new JsonObject
        {
            ["jql"] = jql,
            ["fields"] = new JsonArray(fields.Select(f => JsonValue.Create(f)!).ToArray()),
            ["maxResults"] = 100,
        };
        using var req = NewRequest(HttpMethod.Post, baseUrl + "/rest/api/3/search/jql", email, token);
        req.Content = JsonContent.Create(body);
        using var resp = await _http.SendAsync(req);
        if (!resp.IsSuccessStatusCode) return new();
        var json = await resp.Content.ReadFromJsonAsync<JsonNode>();
        var outp = new List<(string, string, string?, string?, int?)>();
        foreach (var issue in json?["issues"]?.AsArray() ?? new JsonArray())
        {
            if (issue is null) continue;
            var key = issue["key"]!.GetValue<string>();
            var f = issue["fields"];
            var summary = f?["summary"]?.GetValue<string>() ?? "";
            var status = f?["status"];
            // Jira's own "Original Estimate" (timetracking.originalEstimateSeconds) - a real,
            // manually-set field, when the requesting query asked for it. Never invented: a
            // ticket nobody estimated in Jira simply has none here.
            var estimateSecs = f?["timetracking"]?["originalEstimateSeconds"]?.GetValue<int?>();
            int? expectedMinutes = estimateSecs.HasValue ? estimateSecs.Value / 60 : null;
            outp.Add((key, summary, status?["name"]?.GetValue<string>(), status?["statusCategory"]?["key"]?.GetValue<string>(), expectedMinutes));
        }
        return outp;
    }
}
