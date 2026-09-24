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
Console.WriteLine("== DashboardService.RefreshAsync() (Activity feed) ==");
var dashboard = new DashboardService(jira, config, errorLog);
var data = await dashboard.RefreshAsync();
Console.WriteLine($"Tickets={data.Tickets.Count} Activity={data.Activity.Count} Donut={data.Donut.Count}");
foreach (var (ticket, action) in data.Activity.Take(5))
{
    Console.WriteLine($"  - [{action.Kind}] {ticket}: {action.Message} ({action.Minutes}m) @ {action.Timestamp:HH:mm}");
}

Console.WriteLine();
Console.WriteLine("OK - no exceptions.");
