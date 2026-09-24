using System.Windows;
using MyWorkDay.ViewModels;

namespace MyWorkDay.Views;

public partial class SettingsWindow : Window
{
    private readonly SettingsViewModel _vm;

    public SettingsWindow(SettingsViewModel vm)
    {
        _vm = vm;
        DataContext = vm;
        InitializeComponent();
        Loaded += (_, _) =>
        {
            // PasswordBox.Password is deliberately not bindable (WPF security guidance) - seed
            // it once from the view model instead, same masked-value pattern the settings dialog
            // always used (a mask means "unchanged", any edit replaces it).
            TokenBox.Password = _vm.JiraToken;
            GoogleSecretBox.Password = _vm.GoogleClientSecret;
        };
    }

    private void OpenTokenPage_Click(object sender, RoutedEventArgs e)
    {
        try { System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo("https://id.atlassian.com/manage-profile/security/api-tokens") { UseShellExecute = true }); }
        catch { }
    }

    private async void SaveJira_Click(object sender, RoutedEventArgs e)
    {
        _vm.JiraToken = TokenBox.Password;
        _vm.GoogleClientSecret = GoogleSecretBox.Password;
        await _vm.SaveJiraCommand.ExecuteAsync(null);
    }

    private void Close_Click(object sender, RoutedEventArgs e) => Close();
}
