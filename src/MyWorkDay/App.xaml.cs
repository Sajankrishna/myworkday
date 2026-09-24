using System.Windows;
using MyWorkDay.Core;
using MyWorkDay.ViewModels;
using MyWorkDay.Views;

namespace MyWorkDay;

/// <summary>
/// Interaction logic for App.xaml. No DI container - just plain constructor wiring, since this
/// app has a small, fixed service graph (one ConfigService/ErrorLogService/JiraService/etc per
/// process, shared by the main window and the Team Worklogs window).
/// </summary>
public partial class App : Application
{
    public static ConfigService Config { get; private set; } = null!;
    public static ErrorLogService ErrorLog { get; private set; } = null!;
    public static JiraService Jira { get; private set; } = null!;
    public static GoogleCalendarService Calendar { get; private set; } = null!;
    public static ToastService Toast { get; private set; } = null!;
    public static BreakReminderService BreakReminder { get; private set; } = null!;
    public static DashboardService Dashboard { get; private set; } = null!;

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        Config = new ConfigService();
        ErrorLog = new ErrorLogService(Config);
        Jira = new JiraService(Config, ErrorLog);
        Calendar = new GoogleCalendarService(ErrorLog);
        Toast = new ToastService(ErrorLog);
        BreakReminder = new BreakReminderService(Toast, Config.Load().BreakReminderMins ?? 120);
        Dashboard = new DashboardService(Jira, Config, ErrorLog);

        DispatcherUnhandledException += (_, args) =>
        {
            ErrorLog.Log("Unhandled UI exception", args.Exception);
            args.Handled = true; // never crash the window on a single failed operation
        };

        var vm = new MainViewModel(Dashboard, Jira, Calendar, Config, ErrorLog, BreakReminder);
        var window = new MainWindow(vm);
        MainWindow = window;
        window.Show();
        _ = vm.InitializeAsync();
    }
}
