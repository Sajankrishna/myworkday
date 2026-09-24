using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using MyWorkDay.Core;
using MyWorkDay.ViewModels;

namespace MyWorkDay.Views;

public partial class MainWindow : Window
{
    private readonly MainViewModel _vm;

    // Guards against the DatePicker/ComboBox/view-model feedback loop: setting DateJumpPicker's
    // SelectedDate to keep it in sync with the view model would otherwise re-fire
    // DateJumpPicker_SelectedDateChanged and trigger a second, redundant date-change call.
    private bool _syncingDate;

    // Same idea as _syncingDate, for the Team Worklog card's own date picker.
    private bool _syncingTeamDate;

    public MainWindow(MainViewModel vm)
    {
        _vm = vm;
        DataContext = vm;
        InitializeComponent();
        // Neither picker should ever offer a future date - there's no real activity to show
        // there yet. WPF's Calendar automatically greys out/disables anything past
        // DisplayDateEnd, so this alone is enough (no separate BlackoutDates needed).
        DateJumpPicker.DisplayDateEnd = DateTime.Today;
        TeamDatePicker.DisplayDateEnd = DateTime.Today;
        _vm.PropertyChanged += (_, e) =>
        {
            if (e.PropertyName == nameof(MainViewModel.SelectedDate)) SyncDateJumpPicker();
        };
        _vm.TeamVm.PropertyChanged += (_, e) =>
        {
            if (e.PropertyName == nameof(TeamWorklogViewModel.SelectedDate)) SyncTeamDatePicker();
        };
        SyncDateJumpPicker();
        SyncTeamDatePicker();
    }

    private void SyncDateJumpPicker()
    {
        if (!DateTime.TryParseExact(_vm.SelectedDate, "yyyy-MM-dd", null, System.Globalization.DateTimeStyles.None, out var d))
            return;
        _syncingDate = true;
        try { DateJumpPicker.SelectedDate = d; }
        finally { _syncingDate = false; }
    }

    private void SyncTeamDatePicker()
    {
        if (!DateTime.TryParseExact(_vm.TeamVm.SelectedDate, "yyyy-MM-dd", null, System.Globalization.DateTimeStyles.None, out var d))
            return;
        _syncingTeamDate = true;
        try { TeamDatePicker.SelectedDate = d; }
        finally { _syncingTeamDate = false; }
    }

    private async void DateSelect_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (sender is ComboBox { SelectedItem: string date }) await _vm.ChangeDateAsync(date);
    }

    private async void DateJumpPicker_SelectedDateChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_syncingDate || DateJumpPicker.SelectedDate is not { } date) return;
        // A date picked here doesn't have to already be in the "Dates" dropdown (that list only
        // ever has days with real activity, plus today) - any valid calendar date is a legitimate
        // selection (it just shows an empty day), same as the Python build's own date-jump picker.
        await _vm.ChangeDateAsync(date.ToString("yyyy-MM-dd"));
    }

    private void OpenJira_Click(object sender, RoutedEventArgs e)
    {
        if (string.IsNullOrWhiteSpace(_vm.JiraBaseUrl))
        {
            MessageBox.Show("Connect Jira in Settings first.", "MyWorkDay", MessageBoxButton.OK, MessageBoxImage.Information);
            return;
        }
        UrlOpener.Open(_vm.JiraBaseUrl);
    }

    /// <summary>Every clickable ticket key (Tag = the row's JiraUrl) opens it in the browser -
    /// e.Handled stops the click from also bubbling up to a parent row's own toggle handler.</summary>
    private void TicketKey_Click(object sender, MouseButtonEventArgs e)
    {
        if (sender is FrameworkElement { Tag: string url }) UrlOpener.Open(url);
        e.Handled = true;
    }

    private void OpenSettings_Click(object sender, RoutedEventArgs e)
    {
        var settingsVm = new ViewModels.SettingsViewModel(App.Jira, App.Calendar, App.Config, App.BreakReminder);
        var dlg = new SettingsWindow(settingsVm) { Owner = this };
        settingsVm.SettingsChanged += async () => await _vm.RefreshCommand.ExecuteAsync(null);
        dlg.ShowDialog();
    }

    private void OpenNeedsLogging_Click(object sender, RoutedEventArgs e)
    {
        var dlg = new NeedsLoggingWindow(_vm.NeedsLogging) { Owner = this };
        dlg.ShowDialog();
    }

    private async void Refresh_Tile_Click(object sender, MouseButtonEventArgs e) => await _vm.RefreshCommand.ExecuteAsync(null);

    private void ShowWeek_Click(object sender, RoutedEventArgs e) =>
        MessageBox.Show($"Last 7 day(s): {_vm.StatWeekLogged} total, {_vm.StatWeekTickets} ticket touches.",
            "MyWorkDay", MessageBoxButton.OK, MessageBoxImage.Information);

    private void TimeHistory_Click(object sender, RoutedEventArgs e) =>
        MessageBox.Show("Pick a day from the My Work date controls to see its history.",
            "MyWorkDay", MessageBoxButton.OK, MessageBoxImage.Information);

    private void OpenTeamWindow_Click(object sender, MouseButtonEventArgs e)
    {
        // Shares MainViewModel.TeamVm rather than creating a fresh view model, so the popup
        // window and the embedded dashboard card always agree on roster/date/loaded data.
        var win = new TeamWorklogWindow(_vm.TeamVm) { Owner = this };
        win.Show();
        if (!_vm.TeamVm.IsLoaded) _ = _vm.TeamVm.LoadCommand.ExecuteAsync(null);
    }

    private async void TeamDatePicker_SelectedDateChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_syncingTeamDate || TeamDatePicker.SelectedDate is not { } date) return;
        _vm.TeamVm.SelectedDate = date.ToString("yyyy-MM-dd");
        await _vm.TeamVm.RefreshCurrentCommand.ExecuteAsync(null);
    }

    private void TeamMemberRow_Toggle(object sender, MouseButtonEventArgs e)
    {
        if (sender is FrameworkElement { DataContext: TeamMemberRowViewModel row }) row.IsExpanded = !row.IsExpanded;
    }

    private void TicketRow_Toggle(object sender, MouseButtonEventArgs e)
    {
        if (sender is FrameworkElement { DataContext: TicketRowViewModel row }) row.IsExpanded = !row.IsExpanded;
    }

    private void ActivityTicket_Click(object sender, MouseButtonEventArgs e)
    {
        // Run is a FrameworkContentElement, not a FrameworkElement, but it still inherits
        // DataContext from its containing TextBlock the same way.
        if (sender is FrameworkContentElement { DataContext: ActivityItemViewModel item }) UrlOpener.Open(item.JiraUrl);
        e.Handled = true;
    }

    private void LogTime_Click(object sender, RoutedEventArgs e) => OpenLogTime(null);

    private void TicketRowLogTime_Click(object sender, RoutedEventArgs e)
    {
        if (sender is FrameworkElement { Tag: string key }) OpenLogTime(key);
    }

    private void OpenLogTime(string? presetTicketKey)
    {
        var logVm = new LogTimeViewModel(App.Jira, _vm.SelectedDate, presetTicketKey);
        var dlg = new LogTimeWindow(logVm) { Owner = this };
        logVm.PropertyChanged += async (_, e) =>
        {
            if (e.PropertyName == nameof(LogTimeViewModel.Saved) && logVm.Saved)
                await _vm.RefreshCommand.ExecuteAsync(null);
        };
        dlg.ShowDialog();
    }

    private void OpenTicketSearch_Click(object sender, RoutedEventArgs e) => OpenTicketSearch();

    private void OpenTicketSearch()
    {
        var searchVm = new TicketSearchViewModel(App.Jira);
        var dlg = new TicketSearchWindow(searchVm) { Owner = this };
        dlg.ShowDialog();
    }

    private void MainWindow_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.K && Keyboard.Modifiers == ModifierKeys.Control)
        {
            OpenTicketSearch();
            e.Handled = true;
        }
    }
}
