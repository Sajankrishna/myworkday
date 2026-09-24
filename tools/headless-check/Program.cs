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

Console.WriteLine();
Console.WriteLine("== JiraService.SearchTicketsAsync (read-only) ==");
var searchHits = await jira.SearchTicketsAsync("landing page");
Console.WriteLine($"Hits for 'landing page': {searchHits.Count}");
foreach (var h in searchHits.Take(5)) Console.WriteLine($"  - {h.Key} ({h.Status}): {h.Summary}");

var keyHit = await jira.SearchTicketsAsync("CAD-7539");
Console.WriteLine($"Hits for exact key 'CAD-7539': {keyHit.Count}");
foreach (var h in keyHit) Console.WriteLine($"  - {h.Key} ({h.Status}): {h.Summary}");

Console.WriteLine();
Console.WriteLine("OK - no exceptions. (AddWorklogAsync NOT exercised here - it writes real data to Jira.)");
