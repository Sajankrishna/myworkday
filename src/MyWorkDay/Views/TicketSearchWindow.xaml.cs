using System.Windows;
using System.Windows.Input;
using MyWorkDay.Core;
using MyWorkDay.ViewModels;

namespace MyWorkDay.Views;

public partial class TicketSearchWindow : Window
{
    private readonly TicketSearchViewModel _vm;

    public TicketSearchWindow(TicketSearchViewModel vm)
    {
        _vm = vm;
        DataContext = vm;
        InitializeComponent();
        Loaded += (_, _) => QueryBox.Focus();
    }

    private async void QueryBox_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter) await _vm.SearchCommand.ExecuteAsync(null);
    }

    private void Result_Click(object sender, MouseButtonEventArgs e)
    {
        if (sender is not FrameworkElement { Tag: OpenTicketRow row } || row.JiraUrl is null) return;
        try { System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(row.JiraUrl) { UseShellExecute = true }); }
        catch { }
    }
}
