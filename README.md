# MyWorkDay (WPF)

A native Windows desktop app (.NET 8 / WPF / MVVM) that shows your real Jira activity: time
you've logged today, tickets you've touched, comments you've written, and your open assigned
workload - all fetched fresh from Jira Cloud on demand.

This is a rewrite of the original pywebview-based `myworkday_app.py` tool. **It is Jira-only** -
unlike the original, this build does no local git-repo scanning at all. Every figure here traces
back to a real Jira worklog entry, a real comment, or a real assignment.

## Features

- **Dashboard**: today's logged minutes, tickets touched, comments written, open assigned
  tickets, a per-status time breakdown, and a real-time activity feed - built entirely from
  Jira worklogs/comments/assignments for the selected work-day (Eastern time, 10pm cutoff).
- **Break reminder**: a real Windows Action Center toast after N minutes of continuous
  mouse/keyboard activity (via `GetLastInputInfo`), configurable in Settings.
- **Google Calendar**: optional OAuth connection showing today's agenda alongside your work.
- **Team Worklogs**: a second window to search Jira users, add teammates, and see their
  per-ticket logged time for any date.

## Requirements

- Windows 10/11
- .NET 8 SDK (or the `Microsoft.WindowsDesktop.App 8.0` runtime to just run a published build)
- A Jira Cloud account + API token (Settings walks you through creating one)

## Run

```powershell
dotnet run --project src/MyWorkDay
```

## Configuration

Settings (Jira base URL/email/token, Google OAuth client/secret/refresh token, break reminder
minutes, team roster) are stored in `%LOCALAPPDATA%\MyWorkDay\config.json` - the same path and
JSON shape the original Python build used, in plaintext. Errors are logged to
`%LOCALAPPDATA%\MyWorkDay\error.log`.

## Architecture

- `Core/` - services with no UI dependency: `JiraService` (all REST calls), `DashboardService`
  (assembles one day's dashboard data from Jira alone), `GoogleCalendarService`,
  `BreakReminderService`, `ToastService`, `ConfigService`, `ErrorLogService`, plus the shared
  models and the `WorkDate` day-bucketing helper.
- `ViewModels/` - `MainViewModel`, `SettingsViewModel`, `TeamWorklogViewModel` (CommunityToolkit.Mvvm).
- `Views/` - `MainWindow`, `SettingsWindow`, `TeamWorklogWindow`, `NeedsLoggingWindow`.

No dependency injection container - the service graph is small and fixed, wired up once in
`App.xaml.cs`.
