using System.IO;

namespace MyWorkDay.Core;

/// <summary>Appends timestamped entries to error.log next to config.json - the one place a
/// hard-to-reproduce failure leaves a real record instead of silently retrying forever.</summary>
public sealed class ErrorLogService
{
    private const long MaxBytes = 2 * 1024 * 1024;
    private readonly string _path;

    public ErrorLogService(ConfigService config) => _path = config.ErrorLogPath;

    public void Log(string context, Exception? exc = null)
    {
        try
        {
            if (File.Exists(_path) && new FileInfo(_path).Length > MaxBytes)
                File.Move(_path, _path + ".old", overwrite: true);
            var line = $"\n[{DateTime.Now:yyyy-MM-ddTHH:mm:ss}] {context}\n";
            if (exc != null) line += exc + "\n";
            File.AppendAllText(_path, line);
        }
        catch
        {
            // logging must never itself break the app
        }
    }
}
