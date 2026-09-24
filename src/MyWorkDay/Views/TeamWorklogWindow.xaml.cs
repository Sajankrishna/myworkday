using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using MyWorkDay.Core;
using MyWorkDay.ViewModels;

namespace MyWorkDay.Views;

public partial class TeamWorklogWindow : Window
{
    private readonly TeamWorklogViewModel _vm;

    public TeamWorklogWindow(TeamWorklogViewModel vm)
    {
        _vm = vm;
        DataContext = vm;
        InitializeComponent();
        Loaded += (_, _) => DatePickerCtl.SelectedDate = WorkDate.Parse(_vm.SelectedDate);
    }

    private async void DatePicker_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (DatePickerCtl.SelectedDate is { } d)
        {
            _vm.SelectedDate = d.ToString("yyyy-MM-dd");
            await _vm.RefreshCurrentCommand.ExecuteAsync(null);
        }
    }

    private async void SearchBox_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter) await _vm.SearchCommand.ExecuteAsync(null);
    }

    private async void AddMember_Click(object sender, RoutedEventArgs e)
    {
        if (sender is Button { Tag: JiraUserSearchResult user }) await _vm.AddMemberCommand.ExecuteAsync(user);
    }

    private async void RemoveMember_Click(object sender, RoutedEventArgs e)
    {
        if (sender is Button { Tag: TeamMemberRowViewModel row })
        {
            if (MessageBox.Show("Remove this teammate from your team view?", "MyWorkDay", MessageBoxButton.YesNo) != MessageBoxResult.Yes) return;
            await _vm.RemoveMemberCommand.ExecuteAsync(row);
        }
    }

    private void MemberRow_Toggle(object sender, MouseButtonEventArgs e)
    {
        if (sender is FrameworkElement { DataContext: TeamMemberRowViewModel row }) row.IsExpanded = !row.IsExpanded;
    }
}
