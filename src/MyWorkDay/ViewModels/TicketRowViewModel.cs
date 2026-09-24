using CommunityToolkit.Mvvm.ComponentModel;
using MyWorkDay.Core;

namespace MyWorkDay.ViewModels;

/// <summary>Thin, bindable wrapper around a real TicketDayRow - IsExpanded is the only piece
/// of UI-only state (whether the action list is shown), everything else is a straight
/// pass-through of real Jira data.</summary>
public sealed partial class TicketRowViewModel : ObservableObject
{
    public TicketDayRow Row { get; }

    [ObservableProperty]
    private bool _isExpanded;

    public TicketRowViewModel(TicketDayRow row) => Row = row;

    public string Key => Row.Key;
    public string Summary => Row.Summary;
    public string? Status => Row.Status;
    public string? StatusCategory => Row.StatusCategory;
    public string Logged => DashboardStats.FormatMinutes(Row.LoggedMinutes);
    public bool HasLogged => Row.LoggedMinutes > 0;
    public string ActivityKind => Row.ActivityKind;
    public string? JiraUrl => Row.JiraUrl;
    public IReadOnlyList<TicketAction> Actions => Row.Actions;
}
