using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using MyWorkDay.Core;

namespace MyWorkDay.ViewModels;

public sealed partial class SettingsViewModel : ObservableObject
{
    private readonly JiraService _jira;
    private readonly GoogleCalendarService _calendar;
    private readonly ConfigService _config;
    private readonly BreakReminderService _breakReminder;

    public SettingsViewModel(JiraService jira, GoogleCalendarService calendar, ConfigService config, BreakReminderService breakReminder)
    {
        _jira = jira;
        _calendar = calendar;
        _config = config;
        _breakReminder = breakReminder;
        Load();
    }

    [ObservableProperty] private string _jiraBaseUrl = "https://your-domain.atlassian.net";
    [ObservableProperty] private string _jiraEmail = "";
    [ObservableProperty] private string _jiraToken = "";
    [ObservableProperty] private bool _jiraTokenIsMasked;
    [ObservableProperty] private string _jiraStatusMessage = "";
    [ObservableProperty] private bool _jiraStatusOk;

    [ObservableProperty] private string _googleClientId = "";
    [ObservableProperty] private string _googleClientSecret = "";
    [ObservableProperty] private string _calendarStatusMessage = "";
    [ObservableProperty] private bool _calendarStatusOk;

    [ObservableProperty] private int _breakReminderMins = 120;
    [ObservableProperty] private string _breakReminderStatusMessage = "";

    // Comma/space-separated ticket keys - the bounded escape hatch for "I commented on a
    // teammate's ticket, not just my own" (see GetCommentedRowsAsync's own docs for why this
    // can't just be a broader search instead).
    [ObservableProperty] private string _watchedTickets = "";
    [ObservableProperty] private string _watchedTicketsStatusMessage = "";

    // Whole-project, single-day comment scan (see GetProjectDayCommentsAsync) - finds a
    // comment on ANY ticket in the project, not just your own, without needing WatchedTickets.
    [ObservableProperty] private string _projectKey = "";
    [ObservableProperty] private string _projectKeyStatusMessage = "";

    [ObservableProperty] private bool _isBusy;

    public event Action? SettingsChanged;

    private const string TokenMask = "••••••••••••";

    private void Load()
    {
        var cfg = _config.Load();
        JiraBaseUrl = string.IsNullOrWhiteSpace(cfg.JiraBaseUrl) ? "https://your-domain.atlassian.net" : cfg.JiraBaseUrl!;
        JiraEmail = cfg.JiraEmail ?? "";
        JiraTokenIsMasked = !string.IsNullOrWhiteSpace(cfg.JiraApiToken);
        JiraToken = JiraTokenIsMasked ? TokenMask : "";
        JiraStatusMessage = cfg.JiraConnected ? "Currently connected." : "Not connected yet.";
        JiraStatusOk = cfg.JiraConnected;

        GoogleClientId = cfg.GoogleClientId ?? "";
        GoogleClientSecret = "";
        CalendarStatusMessage = cfg.CalendarConnected ? "Currently connected." : "Not connected yet.";
        CalendarStatusOk = cfg.CalendarConnected;

        BreakReminderMins = cfg.BreakReminderMins ?? 120;
        WatchedTickets = string.Join(", ", cfg.WatchedTickets);
        ProjectKey = cfg.ProjectKey ?? "";
    }

    [RelayCommand]
    private async Task SaveJiraAsync()
    {
        IsBusy = true;
        JiraStatusMessage = "Testing connection…";
        try
        {
            var token = JiraToken == TokenMask ? _config.Load().JiraApiToken ?? "" : JiraToken;
            var result = await _jira.TestConnectionAsync(JiraBaseUrl.Trim(), JiraEmail.Trim(), token.Trim());
            if (result.Ok)
            {
                var cfg = _config.Load();
                cfg.JiraBaseUrl = JiraBaseUrl.Trim();
                cfg.JiraEmail = JiraEmail.Trim();
                cfg.JiraApiToken = token.Trim();
                _config.Save(cfg);
                JiraStatusMessage = $"✓ Connected as {result.DisplayName}";
                JiraStatusOk = true;
                SettingsChanged?.Invoke();
            }
            else
            {
                JiraStatusMessage = "✗ " + result.Error;
                JiraStatusOk = false;
            }
        }
        finally
        {
            IsBusy = false;
        }
    }

    [RelayCommand]
    private async Task ConnectGoogleAsync()
    {
        IsBusy = true;
        CalendarStatusMessage = "Opening your browser for Google sign-in…";
        try
        {
            var clientId = string.IsNullOrWhiteSpace(GoogleClientId) ? GoogleCalendarService.SharedClientId : GoogleClientId.Trim();
            var clientSecret = string.IsNullOrWhiteSpace(GoogleClientSecret) ? GoogleCalendarService.SharedClientSecret : GoogleClientSecret.Trim();
            var (ok, refreshToken, error) = await _calendar.ConnectAsync(clientId, clientSecret);
            if (ok)
            {
                var cfg = _config.Load();
                cfg.GoogleClientId = clientId;
                cfg.GoogleClientSecret = clientSecret;
                cfg.GoogleRefreshToken = refreshToken;
                _config.Save(cfg);
                CalendarStatusMessage = "✓ Connected";
                CalendarStatusOk = true;
                SettingsChanged?.Invoke();
            }
            else
            {
                CalendarStatusMessage = "✗ " + error;
                CalendarStatusOk = false;
            }
        }
        finally
        {
            IsBusy = false;
        }
    }

    [RelayCommand]
    private void SaveBreakReminder()
    {
        var mins = Math.Max(0, BreakReminderMins);
        var cfg = _config.Load();
        cfg.BreakReminderMins = mins;
        _config.Save(cfg);
        _breakReminder.ReminderMinutes = mins;
        BreakReminderStatusMessage = mins > 0 ? "✓ Saved" : "✓ Saved - break reminders are off";
    }

    [RelayCommand]
    private void SaveWatchedTickets()
    {
        var keys = WatchedTickets
            .Split(new[] { ',', ' ', ';', '\n', '\r', '\t' }, StringSplitOptions.RemoveEmptyEntries)
            .Select(k => k.Trim().ToUpperInvariant())
            .Where(k => k.Length > 0)
            .Distinct()
            .ToList();
        var cfg = _config.Load();
        cfg.WatchedTickets = keys;
        _config.Save(cfg);
        WatchedTickets = string.Join(", ", keys);
        WatchedTicketsStatusMessage = keys.Count > 0 ? $"✓ Saved ({keys.Count} ticket(s))" : "✓ Saved (none)";
        SettingsChanged?.Invoke();
    }

    [RelayCommand]
    private void SaveProjectKey()
    {
        var key = ProjectKey.Trim().ToUpperInvariant();
        var cfg = _config.Load();
        cfg.ProjectKey = string.IsNullOrWhiteSpace(key) ? null : key;
        _config.Save(cfg);
        ProjectKey = key;
        ProjectKeyStatusMessage = string.IsNullOrWhiteSpace(key)
            ? "✓ Saved - back to assignee/reporter + watched tickets only"
            : $"✓ Saved - My Work now scans all of {key} for your comments";
        SettingsChanged?.Invoke();
    }

    [RelayCommand]
    private void ClearSettings()
    {
        _config.Clear();
        Load();
        SettingsChanged?.Invoke();
    }
}
