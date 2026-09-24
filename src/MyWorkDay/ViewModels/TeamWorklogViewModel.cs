using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using MyWorkDay.Core;

namespace MyWorkDay.ViewModels;

public sealed partial class TeamMemberRowViewModel : ObservableObject
{
    public TeamMemberConfig Member { get; }

    [ObservableProperty] private int _totalMinutes;
    [ObservableProperty] private bool _isExpanded;

    public ObservableCollection<TeamMemberTicket> Tickets { get; } = new();

    public TeamMemberRowViewModel(TeamMemberConfig member) => Member = member;

    public string DisplayName => Member.DisplayName;
    public string Email => Member.Email;
    public string Initials => string.Concat(DisplayName.Split(' ', StringSplitOptions.RemoveEmptyEntries).Take(2).Select(w => char.ToUpper(w[0])));
    public string TotalLabel => DashboardStats.FormatMinutes(TotalMinutes);

    // Traffic-light coding for how much of a full day is logged - same thresholds as the
    // Python build's strengthClass(): red = barely started, orange = partway, green = a full
    // (or fuller) day's worth.
    public int StrengthPct => Math.Max(0, Math.Min(100, (int)Math.Round(100.0 * TotalMinutes / DashboardService.DailyTargetMinutes)));

    partial void OnTotalMinutesChanged(int value)
    {
        OnPropertyChanged(nameof(StrengthPct));
        OnPropertyChanged(nameof(TotalLabel));
    }
}

public sealed partial class TeamWorklogViewModel : ObservableObject
{
    private readonly JiraService _jira;
    private readonly ConfigService _config;

    public TeamWorklogViewModel(JiraService jira, ConfigService config)
    {
        _jira = jira;
        _config = config;
    }

    [ObservableProperty] private string _selectedDate = WorkDate.Today();
    [ObservableProperty] private string _dateLabel = "";
    [ObservableProperty] private bool _isBusy;
    [ObservableProperty] private string _searchQuery = "";

    // Two-stage load, same as the Python build's checkTeamRoster()/loadTeamWorklogNow(): a
    // cheap local config read reveals whether the embedded dashboard card should show at all
    // (HasRoster), without touching the network. The real per-account Jira lookups only run
    // once IsLoaded is explicitly requested (button click, date change, or the popup window
    // opening), so they can never slow down or fail the core dashboard load.
    [ObservableProperty] private bool _hasRoster;
    [ObservableProperty] private bool _isLoaded;

    public ObservableCollection<TeamMemberRowViewModel> Members { get; } = new();
    public ObservableCollection<JiraUserSearchResult> SearchResults { get; } = new();

    /// <summary>Cheap, no-network check of whether a team roster is configured at all - safe
    /// to call on every dashboard refresh.</summary>
    public void CheckRoster()
    {
        HasRoster = _config.Load().TeamMembers.Count > 0;
        DateLabel = WorkDate.Parse(SelectedDate).ToString("ddd, MMM d, yyyy");
    }

    [RelayCommand]
    public async Task LoadAsync()
    {
        var cfg = _config.Load();
        HasRoster = cfg.TeamMembers.Count > 0;
        await RefreshAsync(cfg);
        IsLoaded = true;
    }

    [RelayCommand]
    private async Task SearchAsync()
    {
        SearchResults.Clear();
        if (string.IsNullOrWhiteSpace(SearchQuery)) return;
        var hits = await _jira.SearchUsersAsync(SearchQuery);
        foreach (var h in hits) SearchResults.Add(h);
    }

    [RelayCommand]
    private async Task AddMemberAsync(JiraUserSearchResult user)
    {
        var cfg = _config.Load();
        if (!cfg.TeamMembers.Any(m => m.AccountId == user.AccountId))
        {
            cfg.TeamMembers.Add(new TeamMemberConfig { AccountId = user.AccountId, DisplayName = user.DisplayName, Email = user.Email });
            _config.Save(cfg);
        }
        SearchQuery = "";
        SearchResults.Clear();
        HasRoster = true;
        await RefreshAsync(cfg);
        IsLoaded = true;
    }

    [RelayCommand]
    private async Task RemoveMemberAsync(TeamMemberRowViewModel row)
    {
        var cfg = _config.Load();
        cfg.TeamMembers.RemoveAll(m => m.AccountId == row.Member.AccountId);
        _config.Save(cfg);
        HasRoster = cfg.TeamMembers.Count > 0;
        await RefreshAsync(cfg);
    }

    [RelayCommand]
    public async Task RefreshCurrentAsync() => await RefreshAsync(_config.Load());

    private async Task RefreshAsync(AppConfig cfg)
    {
        IsBusy = true;
        try
        {
            DateLabel = WorkDate.Parse(SelectedDate).ToString("ddd, MMM d, yyyy");
            var team = cfg.TeamMembers;
            var results = new List<(TeamMemberConfig Member, List<TeamMemberTicket> Tickets, int Total)>();
            var tasks = team.Select(async m =>
            {
                var (tickets, total) = await _jira.GetTeamMemberTicketsAsync(m.AccountId, SelectedDate);
                return (m, tickets, total);
            });
            results = (await Task.WhenAll(tasks)).ToList();
            results = results.OrderByDescending(r => r.Total).ToList();

            var openIds = Members.Where(m => m.IsExpanded).Select(m => m.Member.AccountId).ToHashSet();
            Members.Clear();
            foreach (var (member, tickets, total) in results)
            {
                var row = new TeamMemberRowViewModel(member) { TotalMinutes = total, IsExpanded = openIds.Contains(member.AccountId) };
                foreach (var t in tickets) row.Tickets.Add(t);
                Members.Add(row);
            }
        }
        finally
        {
            IsBusy = false;
        }
    }
}
