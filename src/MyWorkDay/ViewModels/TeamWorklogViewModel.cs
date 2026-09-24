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

    public ObservableCollection<TeamMemberRowViewModel> Members { get; } = new();
    public ObservableCollection<JiraUserSearchResult> SearchResults { get; } = new();

    public async Task LoadAsync()
    {
        var cfg = _config.Load();
        await RefreshAsync(cfg);
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
        await RefreshAsync(cfg);
    }

    [RelayCommand]
    private async Task RemoveMemberAsync(TeamMemberRowViewModel row)
    {
        var cfg = _config.Load();
        cfg.TeamMembers.RemoveAll(m => m.AccountId == row.Member.AccountId);
        _config.Save(cfg);
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
