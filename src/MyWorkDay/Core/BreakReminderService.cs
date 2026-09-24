using System.Runtime.InteropServices;

namespace MyWorkDay.Core;

/// <summary>Tracks one continuous "active" stretch using real system-wide input
/// (GetLastInputInfo, the same Windows API the OS itself uses for screensaver/lock timing) - a
/// gap of IdleResetMins with no mouse/keyboard input resets the stretch. Crossing the
/// configured reminder threshold fires a toast, repeated every RepeatMins until an actual
/// break happens. Runs on a plain background timer on the UI thread's dispatcher context (WPF
/// has no cross-thread-into-window hazard here, unlike the old pywebview build) so there's
/// nothing unsafe about updating bound properties directly from the tick.</summary>
public sealed class BreakReminderService : IDisposable
{
    private const int IdleResetMins = 5;
    private const int RepeatMins = 30;

    [StructLayout(LayoutKind.Sequential)]
    private struct LASTINPUTINFO
    {
        public uint cbSize;
        public uint dwTime;
    }

    [DllImport("user32.dll")]
    private static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);

    private readonly ToastService _toast;
    private readonly System.Timers.Timer _timer;
    private DateTime _workStart = DateTime.UtcNow;
    private DateTime? _lastNotifiedAt;

    public int ReminderMinutes { get; set; }
    public double ContinuousMinutes { get; private set; }

    public event Action? StatusChanged;

    public BreakReminderService(ToastService toast, int initialReminderMinutes)
    {
        _toast = toast;
        ReminderMinutes = initialReminderMinutes;
        _timer = new System.Timers.Timer(60_000) { AutoReset = true };
        _timer.Elapsed += (_, _) => Tick();
    }

    public void Start() => _timer.Start();

    private static double GetIdleSeconds()
    {
        try
        {
            var info = new LASTINPUTINFO();
            info.cbSize = (uint)Marshal.SizeOf<LASTINPUTINFO>();
            if (!GetLastInputInfo(ref info)) return 0;
            var idleTicks = (uint)Environment.TickCount - info.dwTime;
            return Math.Max(0, idleTicks / 1000.0);
        }
        catch { return 0; }
    }

    private void Tick()
    {
        var now = DateTime.UtcNow;
        var idleSecs = GetIdleSeconds();
        if (idleSecs >= IdleResetMins * 60)
        {
            _workStart = now.AddSeconds(-idleSecs);
            _lastNotifiedAt = null;
            ContinuousMinutes = 0;
        }
        else
        {
            ContinuousMinutes = (now - _workStart).TotalMinutes;
            if (ReminderMinutes > 0 && ContinuousMinutes >= ReminderMinutes &&
                (_lastNotifiedAt is null || (now - _lastNotifiedAt.Value).TotalMinutes >= RepeatMins))
            {
                _toast.Show("Time for a break",
                    $"You've been working for {(int)ContinuousMinutes} minutes straight. " +
                    "Stand up, stretch, look away from the screen for a bit.");
                _lastNotifiedAt = now;
            }
        }
        StatusChanged?.Invoke();
    }

    public void Dispose() => _timer.Dispose();
}
