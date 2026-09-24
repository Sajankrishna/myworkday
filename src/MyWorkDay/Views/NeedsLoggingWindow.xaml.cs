using System.Collections.ObjectModel;
using System.Windows;
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
}
