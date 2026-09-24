using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using MyWorkDay.Core;

namespace MyWorkDay.ViewModels;

/// <summary>Backs the "+ Log Time" dialog - posts a real Jira worklog entry for the ticket you
/// pick, dated to whatever day the dashboard has selected. Nothing here is estimated or
/// simulated: Save either succeeds against real Jira or reports the real error back.</summary>
public sealed partial class LogTimeViewModel : ObservableObject
{
    private readonly JiraService _jira;

    public string DateKey { get; }
    public string DateLabel { get; }

    public LogTimeViewModel(JiraService jira, string dateKey, string? presetTicketKey = null)
    {
        _jira = jira;
        DateKey = dateKey;
        DateLabel = WorkDate.Parse(dateKey).ToString("ddd, MMM d, yyyy");
        if (!string.IsNullOrWhiteSpace(presetTicketKey)) TicketKey = presetTicketKey;
    }

    [ObservableProperty] private string _ticketKey = "";
    [ObservableProperty] private string _ticketSearchQuery = "";
    [ObservableProperty] private int _hours;
    [ObservableProperty] private int _minutes = 15;
    [ObservableProperty] private string _comment = "";
    [ObservableProperty] private bool _isBusy;
    [ObservableProperty] private string _statusMessage = "";
    [ObservableProperty] private bool _statusOk;
    [ObservableProperty] private bool _saved;

    public ObservableCollection<OpenTicketRow> SearchResults { get; } = new();

    [RelayCommand]
    private async Task SearchTicketsAsync()
    {
        SearchResults.Clear();
        if (string.IsNullOrWhiteSpace(TicketSearchQuery)) return;
        var hits = await _jira.SearchTicketsAsync(TicketSearchQuery);
        foreach (var h in hits) SearchResults.Add(h);
    }

    [RelayCommand]
    private void PickTicket(OpenTicketRow row)
    {
        TicketKey = row.Key;
        SearchResults.Clear();
        TicketSearchQuery = "";
    }

    [RelayCommand]
    private async Task SaveAsync()
    {
        var key = TicketKey.Trim().ToUpperInvariant();
        var totalMinutes = Hours * 60 + Minutes;
        if (string.IsNullOrWhiteSpace(key))
        {
            StatusMessage = "Enter or pick a ticket key.";
            StatusOk = false;
            return;
        }
        if (totalMinutes <= 0)
        {
            StatusMessage = "Enter a time greater than zero.";
            StatusOk = false;
            return;
        }

        IsBusy = true;
        StatusMessage = "Logging to Jira…";
        try
        {
            var startedLocal = ResolveStartedLocal();
            var (ok, error) = await _jira.AddWorklogAsync(key, totalMinutes, string.IsNullOrWhiteSpace(Comment) ? null : Comment.Trim(), startedLocal);
            if (ok)
            {
                StatusMessage = $"✓ Logged {DashboardStats.FormatMinutes(totalMinutes)} to {key}";
                StatusOk = true;
                Saved = true;
            }
            else
            {
                StatusMessage = "✗ " + error;
                StatusOk = false;
            }
        }
        finally
        {
            IsBusy = false;
        }
    }

    private DateTimeOffset ResolveStartedLocal()
    {
        // "Now" when logging against today - a real, honest timestamp. For a past day being
        // backfilled there's no real moment to use, so noon on that date is the least
        // arbitrary-looking placeholder (Jira only really cares about the date for reporting).
        if (DateKey == WorkDate.Today()) return DateTimeOffset.Now;
        var day = WorkDate.Parse(DateKey);
        var noon = new DateTime(day.Year, day.Month, day.Day, 12, 0, 0, DateTimeKind.Unspecified);
        var offset = WorkDate.Eastern.GetUtcOffset(noon);
        return new DateTimeOffset(noon, offset);
    }
}
