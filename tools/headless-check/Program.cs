using MyWorkDay.Core;
using MyWorkDay.ViewModels;

var config = new ConfigService();
var errorLog = new ErrorLogService(config);
var jira = new JiraService(config, errorLog);

Console.WriteLine("== TeamWorklogViewModel.LoadAsync() ==");
var teamVm = new TeamWorklogViewModel(jira, config);
await teamVm.LoadAsync();
Console.WriteLine($"HasRoster={teamVm.HasRoster} IsLoaded={teamVm.IsLoaded} Members={teamVm.Members.Count} DateLabel={teamVm.DateLabel}");
foreach (var m in teamVm.Members)
{
    Console.WriteLine($"  - {m.DisplayName}: {m.TotalLabel} ({m.TotalMinutes}m) strength={m.StrengthPct}% tickets={m.Tickets.Count}");
}

Console.WriteLine();
Console.WriteLine("== DashboardService.RefreshAsync() (today) ==");
var dashboard = new DashboardService(jira, config, errorLog);
var todayData = await dashboard.RefreshAsync();
Console.WriteLine($"SelectedDate={todayData.SelectedDate} (WorkDate.Today={WorkDate.Today()}) Tickets={todayData.Tickets.Count}");
foreach (var t in todayData.Tickets) Console.WriteLine($"  - {t.Key} [{t.ActivityKind}] logged={t.LoggedMinutes}m");
Console.WriteLine($"NeedsLogging={todayData.NeedsLogging.Count}");
foreach (var n in todayData.NeedsLogging) Console.WriteLine($"  - {n.Key} ({n.Status}) expected={(n.ExpectedMinutes is { } m ? m + "m" : "none")}");

var pastDateKey = WorkDate.KeyFor(DateTimeOffset.UtcNow.AddDays(-3));
Console.WriteLine();
Console.WriteLine($"== DashboardService.GetDataAsync({pastDateKey}) (past day) ==");
var pastData = await dashboard.GetDataAsync(pastDateKey);
Console.WriteLine($"SelectedDate={pastData.SelectedDate} Tickets={pastData.Tickets.Count}");
foreach (var t in pastData.Tickets) Console.WriteLine($"  - {t.Key} [{t.ActivityKind}] logged={t.LoggedMinutes}m");

Console.WriteLine();
Console.WriteLine("== MainViewModel.ChangeDateAsync (full VM/UI-layer path) ==");
var calendar = new GoogleCalendarService(errorLog);
var breakReminder = new BreakReminderService(new ToastService(errorLog), 0);
var mvm = new MainViewModel(dashboard, jira, calendar, config, errorLog, breakReminder);
await mvm.RefreshCommand.ExecuteAsync(null);
Console.WriteLine($"After initial refresh: SelectedDate={mvm.SelectedDate} Tickets={mvm.Tickets.Count}");
foreach (var t in mvm.Tickets) Console.WriteLine($"  - {t.Key} [{t.ActivityKind}]");

await mvm.ChangeDateAsync(pastDateKey);
Console.WriteLine($"After ChangeDateAsync({pastDateKey}): SelectedDate={mvm.SelectedDate} Tickets={mvm.Tickets.Count}");
foreach (var t in mvm.Tickets) Console.WriteLine($"  - {t.Key} [{t.ActivityKind}]");

Console.WriteLine();
Console.WriteLine("== JiraService.GetCommentedRowsAsync (raw, today's diagnosis) ==");
var myAccountId = await jira.GetMyAccountIdAsync();
Console.WriteLine($"accountId={myAccountId}");
if (myAccountId != null)
{
    var commented = await jira.GetCommentedRowsAsync(myAccountId, 7);
    Console.WriteLine($"Candidate tickets with >=1 of my comments in the last 7 days: {commented.Count}");
    foreach (var (key, row) in commented)
    {
        Console.WriteLine($"  {key}: {row.Actions.Count} of my comments");
        foreach (var a in row.Actions)
            Console.WriteLine($"    @ {a.Timestamp:yyyy-MM-dd HH:mm:ss zzz} -> WorkDate key = {WorkDate.KeyFor(a.Timestamp)}");
    }
}

// GetCommentedRowsAsync's extraKeys parameter ("watched tickets" in Settings) is the escape
// hatch for a comment on a ticket you're neither assignee nor reporter on - confirmed against
// CAD-6247 (owned by a teammate), where an unwatched lookup misses it entirely but passing it
// as an extra key finds the real comment and buckets it to the right work-day.
Console.WriteLine();
Console.WriteLine("== GetCommentedRowsAsync with an explicit watched ticket ==");
if (myAccountId != null)
{
    foreach (var watchedKey in new[] { "CAD-6247", "CAD-6868" })
    {
        var watched = await jira.GetCommentedRowsAsync(myAccountId, 7, new[] { watchedKey });
        if (watched.TryGetValue(watchedKey, out var r))
            Console.WriteLine($"{watchedKey}: {r.Actions.Count} comment(s) in last 7 days -> " +
                string.Join(", ", r.Actions.Select(a => WorkDate.KeyFor(a.Timestamp))));
        else
            Console.WriteLine($"{watchedKey}: no comment from me in the last 7 days");
    }
}

Console.WriteLine();
Console.WriteLine("== JiraService.SearchTicketsAsync (read-only) ==");
var searchHits = await jira.SearchTicketsAsync("landing page");
Console.WriteLine($"Hits for 'landing page': {searchHits.Count}");
foreach (var h in searchHits.Take(5)) Console.WriteLine($"  - {h.Key} ({h.Status}): {h.Summary}");

var keyHit = await jira.SearchTicketsAsync("CAD-7539");
Console.WriteLine($"Hits for exact key 'CAD-7539': {keyHit.Count}");
foreach (var h in keyHit) Console.WriteLine($"  - {h.Key} ({h.Status}): {h.Summary}");

// One-off scan: for every teammate on the roster, find their recently-updated assigned
// tickets and check each for a real comment by me - bounded (one JQL per teammate, not
// project-wide) so it's actually usable, unlike a whole-project scan.
Console.WriteLine();
Console.WriteLine("== Scanning teammates' tickets for my un-watched comments (last 14 days) ==");
if (myAccountId != null)
{
    var cfgNow = config.Load();
    var alreadyWatched = cfgNow.WatchedTickets.Select(k => k.ToUpperInvariant()).ToHashSet();
    using var http = new System.Net.Http.HttpClient();
    var auth = Convert.ToBase64String(System.Text.Encoding.UTF8.GetBytes($"{cfgNow.JiraEmail}:{cfgNow.JiraApiToken}"));
    var found = new List<(string Key, string Summary, string Assignee)>();

    foreach (var member in cfgNow.TeamMembers)
    {
        if (member.AccountId == myAccountId) continue; // that's "me", already covered

        var jql = $"assignee = \"{member.AccountId}\" AND updated >= \"-14d\" ORDER BY updated DESC";
        var body = System.Text.Json.JsonSerializer.Serialize(new { jql, fields = new[] { "summary" }, maxResults = 50 });
        using var req = new System.Net.Http.HttpRequestMessage(System.Net.Http.HttpMethod.Post,
            cfgNow.JiraBaseUrl!.TrimEnd('/') + "/rest/api/3/search/jql");
        req.Headers.Authorization = new System.Net.Http.Headers.AuthenticationHeaderValue("Basic", auth);
        req.Content = new System.Net.Http.StringContent(body, System.Text.Encoding.UTF8, "application/json");
        using var resp = await http.SendAsync(req);
        if (!resp.IsSuccessStatusCode) { Console.WriteLine($"  {member.DisplayName}: search failed ({resp.StatusCode})"); continue; }
        var doc = System.Text.Json.JsonDocument.Parse(await resp.Content.ReadAsStringAsync());
        var issues = doc.RootElement.GetProperty("issues").EnumerateArray()
            .Select(i => (Key: i.GetProperty("key").GetString()!, Summary: i.GetProperty("fields").GetProperty("summary").GetString() ?? ""))
            .Where(i => !alreadyWatched.Contains(i.Key))
            .ToList();
        Console.WriteLine($"  {member.DisplayName}: {issues.Count} candidate ticket(s) to check");

        foreach (var issue in issues)
        {
            using var creq = new System.Net.Http.HttpRequestMessage(System.Net.Http.HttpMethod.Get,
                cfgNow.JiraBaseUrl!.TrimEnd('/') + $"/rest/api/3/issue/{issue.Key}/comment?maxResults=100&orderBy=-created");
            creq.Headers.Authorization = new System.Net.Http.Headers.AuthenticationHeaderValue("Basic", auth);
            using var cresp = await http.SendAsync(creq);
            if (!cresp.IsSuccessStatusCode) continue;
            var cdoc = System.Text.Json.JsonDocument.Parse(await cresp.Content.ReadAsStringAsync());
            var hasMyComment = cdoc.RootElement.GetProperty("comments").EnumerateArray()
                .Any(c => c.GetProperty("author").GetProperty("accountId").GetString() == myAccountId);
            if (hasMyComment) found.Add((issue.Key, issue.Summary, member.DisplayName));
        }
    }

    Console.WriteLine($"Found {found.Count} un-watched ticket(s) with a real comment from me:");
    foreach (var f in found) Console.WriteLine($"  - {f.Key} (assigned to {f.Assignee}): {f.Summary}");

    if (found.Count > 0)
    {
        var toAdd = found.Select(f => f.Key).Where(k => !cfgNow.WatchedTickets.Contains(k)).ToList();
        if (toAdd.Count > 0)
        {
            cfgNow.WatchedTickets.AddRange(toAdd);
            config.Save(cfgNow);
            Console.WriteLine($"Added to WATCHED_TICKETS: {string.Join(", ", toAdd)}");
        }
    }
}

Console.WriteLine();
Console.WriteLine("OK - no exceptions. (AddWorklogAsync NOT exercised here - it writes real data to Jira.)");
