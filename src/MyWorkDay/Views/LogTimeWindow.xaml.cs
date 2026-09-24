using System.Windows;
using System.Windows.Input;
using MyWorkDay.Core;
using MyWorkDay.ViewModels;

namespace MyWorkDay.Views;

public partial class LogTimeWindow : Window
{
    private readonly LogTimeViewModel _vm;

    public LogTimeWindow(LogTimeViewModel vm)
    {
        _vm = vm;
        DataContext = vm;
        InitializeComponent();
        _vm.PropertyChanged += (_, e) =>
        {
            // Close automatically a moment after a successful save, same "confirm then
            // dismiss" pattern Settings uses - the status message stays visible just long
            // enough to register before the window goes away.
            if (e.PropertyName == nameof(LogTimeViewModel.Saved) && _vm.Saved)
            {
                var timer = new System.Windows.Threading.DispatcherTimer { Interval = TimeSpan.FromMilliseconds(900) };
                timer.Tick += (_, _) => { timer.Stop(); Close(); };
                timer.Start();
            }
        };
    }

    private async void SearchBox_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter) await _vm.SearchTicketsCommand.ExecuteAsync(null);
    }

    private void SearchResult_Click(object sender, MouseButtonEventArgs e)
    {
        if (sender is FrameworkElement { Tag: OpenTicketRow row }) _vm.PickTicketCommand.Execute(row);
    }

    private void Close_Click(object sender, RoutedEventArgs e) => Close();
}
