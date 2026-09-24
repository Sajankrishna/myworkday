using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using MyWorkDay.Core;

namespace MyWorkDay.ViewModels;

/// <summary>Backs the quick ticket search dialog (Ctrl+K) - a real, instance-wide Jira text/key
/// search, not scoped to "my" issues. Results are exactly what SearchTicketsAsync returns,
/// nothing filtered or re-ranked client-side.</summary>
public sealed partial class TicketSearchViewModel : ObservableObject
{
    private readonly JiraService _jira;

    public TicketSearchViewModel(JiraService jira) => _jira = jira;

    [ObservableProperty] private string _query = "";
    [ObservableProperty] private bool _isBusy;
    [ObservableProperty] private bool _searched;

    public ObservableCollection<OpenTicketRow> Results { get; } = new();

    [RelayCommand]
    private async Task SearchAsync()
    {
        if (string.IsNullOrWhiteSpace(Query)) return;
        IsBusy = true;
        Results.Clear();
        try
        {
            var hits = await _jira.SearchTicketsAsync(Query);
            foreach (var h in hits) Results.Add(h);
            Searched = true;
        }
        finally
        {
            IsBusy = false;
        }
    }
}
