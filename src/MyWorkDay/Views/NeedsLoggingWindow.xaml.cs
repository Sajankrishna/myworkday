using System.Collections.ObjectModel;
using System.Windows;
using System.Windows.Input;
using MyWorkDay.Core;

namespace MyWorkDay.Views;

public partial class NeedsLoggingWindow : Window
{
    public NeedsLoggingWindow(ObservableCollection<OpenTicketRow> items)
    {
        InitializeComponent();
        ListHost.ItemsSource = items;
    }

    private void Close_Click(object sender, RoutedEventArgs e) => Close();

    private void TicketKey_Click(object sender, MouseButtonEventArgs e)
    {
        if (sender is FrameworkElement { Tag: string url }) UrlOpener.Open(url);
        e.Handled = true;
    }
}
