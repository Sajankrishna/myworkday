using System.Collections.ObjectModel;
using System.Windows.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using MyWorkDay.Core;

namespace MyWorkDay.ViewModels;

public sealed partial class MainViewModel : ObservableObject
{
    private readonly DashboardService _dashboard;
    private readonly JiraService _jira;
    private readonly GoogleCalendarService _calendar;
    private readonly ConfigService _config;
    private readonly ErrorLogService _errorLog;
    private readonly BreakReminderService _breakReminder;
    private readonly DispatcherTimer _autoRefreshTimer;
    private readonly DispatcherTimer _teamAutoRefreshTimer;

    private DashboardData? _lastData;

    /// <summary>Shared with the Team Worklogs popup window (MainWindow passes this same
    /// instance to it) so the embedded dashboard card and the full window never disagree about
    /// roster or the currently loaded date - one source of truth, same as the Python build's
    /// single get_team_worklogs() backend state.</summary>
    public TeamWorklogViewModel TeamVm { get; }

    public MainViewModel(
        DashboardService dashboard,
        JiraService jira,
        GoogleCalendarService calendar,
        ConfigService config,
        ErrorLogService errorLog,
        BreakReminderService breakReminder)
    {
        _dashboard = dashboard;
        _jira = jira;
        _calendar = calendar;
        _config = config;
        _errorLog = errorLog;
        _breakReminder = breakReminder;
        _breakReminder.StatusChanged += OnBreakStatusChanged;
        TeamVm = new TeamWorklogViewModel(jira, config);

        // Mirrors the Python build's 5-minute auto-refresh for the main dashboard, and its
        // longer 15-minute cadence for team worklogs (real per-teammate Jira lookups, more
        // expensive than the core refresh).
        _autoRefreshTimer = new DispatcherTimer { Interval = TimeSpan.FromMinutes(5) };
        _autoRefreshTimer.Tick += async (_, _) => { if (!IsBusy) await RefreshAsync(); };
        _teamAutoRefreshTimer = new DispatcherTimer { Interval = TimeSpan.FromMinutes(15) };
        _teamAutoRefreshTimer.Tick += async (_, _) => { if (TeamVm.IsLoaded && !TeamVm.IsBusy) await TeamVm.RefreshCurrentCommand.ExecuteAsync(null); };
    }

    // ---- Header / greeting ----
    [ObservableProperty] private string _authorName = "there";
    [ObservableProperty] private string _initials = "?";
    [ObservableProperty] private string _greeting = "Hello,";
    [ObservableProperty] private string _syncedAt = "Last synced —";
    [ObservableProperty] private bool _isBusy;
    [ObservableProperty] private string? _calendarSummary;
    [ObservableProperty] private string _breakStatusText = "";
    [ObservableProperty] private bool _breakStatusVisible;
    [ObservableProperty] private bool _breakStatusDue;

    // ---- Config-derived flags ----
    [ObservableProperty] private bool _jiraConnected;
    [ObservableProperty] private bool _calendarConnected;
    [ObservableProperty] private string? _jiraBaseUrl;

    // ---- Stats ----
    [ObservableProperty] private int _statLoggedMinutes;
    [ObservableProperty] private string _statLogged = "0m";
    [ObservableProperty] private string _statTargetLabel = "";
    [ObservableProperty] private int _statTargetPct;
    [ObservableProperty] private int _statTicketsTouched;
    [ObservableProperty] private int _statOpenAssigned;
    [ObservableProperty] private int _statCommentsMade;
    [ObservableProperty] private string _statWeekLogged = "0m";
    [ObservableProperty] private int _statWeekTickets;

    // ---- Date selection ----
    [ObservableProperty] private string _selectedDate = WorkDate.Today();
    [ObservableProperty] private string _selectedDateLabel = "";
    public ObservableCollection<string> Dates { get; } = new();

    // Decorative only, never a data claim - picked deterministically from the selected date
    // (same hash-and-mod trick the Python build used) so it doesn't flicker to a different line
    // on every re-render of the same day.
    private static readonly string[] Quotes =
    {
        "Small steps make big progress.",
        "Consistent progress beats perfect days.",
        "Every logged minute is a step forward.",
        "Focus on today's ticket, not the whole backlog.",
        "Shipped is better than perfect.",
    };
    [ObservableProperty] private string _dailyQuote = Quotes[0];

    private static string QuoteFor(string dateKey)
    {
        uint h = 0;
        foreach (var ch in dateKey) h = h * 31 + ch;
        return Quotes[h % Quotes.Length];
    }

    // ---- Collections ----
    public ObservableCollection<TicketRowViewModel> Tickets { get; } = new();
    public ObservableCollection<OpenTicketRow> NeedsLogging { get; } = new();
    public ObservableCollection<ActivityItemViewModel> Activity { get; } = new();
    public ObservableCollection<DonutSlice> Donut { get; } = new();
    public ObservableCollection<CalendarEventRow> CalendarEvents { get; } = new();

    partial void OnStatTargetPctChanged(int value) { /* bound directly to a ProgressBar */ }

    public async Task InitializeAsync()
    {
        var cfg = _config.Load();
        _breakReminder.ReminderMinutes = cfg.BreakReminderMins ?? 120;
        _breakReminder.Start();
        _autoRefreshTimer.Start();
        _teamAutoRefreshTimer.Start();
        await RefreshAsync();
        await SyncCalendarAsync();
    }

    [RelayCommand(CanExecute = nameof(CanRunBridgeCall))]
    private async Task RefreshAsync()
    {
        IsBusy = true;
        try
        {
            var cfg = _config.Load();
            JiraConnected = cfg.JiraConnected;
            CalendarConnected = cfg.CalendarConnected;
            var data = await _dashboard.RefreshAsync(SelectedDate);
            Apply(data);
        }
        catch (Exception ex)
        {
            _errorLog.Log("MainViewModel.RefreshAsync failed", ex);
        }
        finally
        {
            IsBusy = false;
        }
    }

    private bool CanRunBridgeCall() => !IsBusy;

    partial void OnIsBusyChanged(bool value) => RefreshCommand.NotifyCanExecuteChanged();

    public async Task ChangeDateAsync(string dateKey)
    {
        if (IsBusy || string.IsNullOrWhiteSpace(dateKey) || dateKey == SelectedDate) return;
        IsBusy = true;
        try
        {
            var data = await _dashboard.GetDataAsync(dateKey);
            Apply(data);
        }
        finally
        {
            IsBusy = false;
        }
    }

    private async Task SyncCalendarAsync()
    {
        var cfg = _config.Load();
        if (!cfg.CalendarConnected) return;
        var result = await _calendar.FetchTodayAsync(
            string.IsNullOrWhiteSpace(cfg.GoogleClientId) ? GoogleCalendarService.SharedClientId : cfg.GoogleClientId!,
            string.IsNullOrWhiteSpace(cfg.GoogleClientSecret) ? GoogleCalendarService.SharedClientSecret : cfg.GoogleClientSecret!,
            cfg.GoogleRefreshToken!);
        if (result is null) return;

        var now = DateTimeOffset.Now;
        CalendarSummary = result.Count == 0
            ? "\U0001F4C5 No meetings today"
            : $"\U0001F4C5 {result.Count} today";

        CalendarEvents.Clear();
        foreach (var e in result.Events)
        {
            string? duration = null;
            if (e.End is { } end)
            {
                var mins = (int)Math.Round((end - e.Start).TotalMinutes);
                if (mins > 0) duration = DashboardStats.FormatMinutes(mins);
            }
            CalendarEvents.Add(new CalendarEventRow
            {
                Time = e.Start.ToLocalTime().ToString("h:mm tt").TrimStart('0'),
                Title = e.Title,
                Duration = duration,
                Source = e.Source,
                IsPast = (e.End ?? e.Start) <= now,
            });
        }
    }

    private void Apply(DashboardData data)
    {
        _lastData = data;
        AuthorName = data.Author;
        Initials = data.Initials;
        Greeting = GreetingFor(data.Hour) + ",";
        SyncedAt = "Last synced " + data.SyncedAt;
        SelectedDate = data.SelectedDate;
        SelectedDateLabel = data.SelectedDateLabel;
        DailyQuote = QuoteFor(data.SelectedDate);
        JiraBaseUrl = data.JiraBaseUrl;

        Dates.Clear();
        foreach (var d in data.Dates) Dates.Add(d);
        // Dates.Clear() just wiped the ComboBox's own SelectedItem to null - and since
        // SelectedDate's setter above only raises PropertyChanged when the value actually
        // differs (a refresh usually reloads the SAME date), that silent no-op means the
        // OneWay-bound ComboBox never gets told to reselect anything, so it's left showing
        // nothing even though the correct date is still right there in the rebuilt list.
        // Force the notification unconditionally so the dropdown always re-picks the current
        // date after every Dates rebuild, refresh or not.
        OnPropertyChanged(nameof(SelectedDate));

        var s = data.Stats;
        StatLoggedMinutes = s.LoggedMinutes;
        StatLogged = s.Logged;
        StatTargetLabel = $"Daily target: {s.DailyTargetMinutes / 60}h";
        StatTargetPct = s.TargetPct;
        StatTicketsTouched = s.TicketsTouched;
        StatOpenAssigned = s.OpenAssigned;
        StatCommentsMade = s.CommentsMade;
        StatWeekLogged = s.WeekLogged;
        StatWeekTickets = s.WeekTickets;

        Tickets.Clear();
        foreach (var t in data.Tickets) Tickets.Add(new TicketRowViewModel(t));

        NeedsLogging.Clear();
        foreach (var n in data.NeedsLogging) NeedsLogging.Add(n);

        Activity.Clear();
        foreach (var (ticket, action) in data.Activity) Activity.Add(new ActivityItemViewModel(ticket, action));

        Donut.Clear();
        foreach (var d in data.Donut) Donut.Add(d);

        TeamVm.CheckRoster();
    }

    private static string GreetingFor(int hour) => hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";

    private void OnBreakStatusChanged()
    {
        var mins = _breakReminder.ContinuousMinutes;
        var threshold = _breakReminder.ReminderMinutes;
        if (threshold <= 0 || mins < 10)
        {
            BreakStatusVisible = false;
            return;
        }
        BreakStatusVisible = true;
        BreakStatusDue = mins >= threshold;
        var label = DashboardStats.FormatMinutes((int)mins);
        BreakStatusText = (BreakStatusDue ? "⚠ Take a break · " : "⏱ Active ") + label + " straight";
    }
}

public sealed class ActivityItemViewModel
{
    public string Ticket { get; }
    public TicketAction Action { get; }
    public ActivityItemViewModel(string ticket, TicketAction action) { Ticket = ticket; Action = action; }

    public string Time => Action.TimeLabel;
    public string Kind => Action.KindLabel;
    public string KindKey => Action.Kind; // "worklog" | "comment" - drives the timeline dot color
    public string Message => Action.Message;

    // Same headline shape the Python build's timeline used: "Logged Xm on " (real logged time
    // takes priority) or "Commented on " / etc, with the ticket key appended separately so it
    // can be colored/bolded on its own in the XAML.
    public string HeadlinePrefix => Action.Minutes > 0
        ? $"Logged {DashboardStats.FormatMinutes(Action.Minutes)} on "
        : $"{Kind} on ";
}
