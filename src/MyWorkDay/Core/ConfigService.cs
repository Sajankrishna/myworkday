using System.IO;
using System.Text.Json;

namespace MyWorkDay.Core;

/// <summary>Loads/saves %LOCALAPPDATA%\MyWorkDay\config.json - same path and JSON shape the
/// Python build used, so an existing install's saved Jira token / Google refresh token / team
/// roster keep working here. Plaintext on disk (matches the original) - see
/// [[myworkday-config-secrets-no-full-read]]-style discipline: never log/print this file's
/// contents wholesale anywhere in this codebase either.</summary>
public sealed class ConfigService
{
    private static readonly JsonSerializerOptions JsonOpts = new() { WriteIndented = true };

    public string ConfigDir { get; }
    public string ConfigPath { get; }
    public string ErrorLogPath { get; }

    public ConfigService()
    {
        var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        ConfigDir = Path.Combine(localAppData, "MyWorkDay");
        Directory.CreateDirectory(ConfigDir);
        ConfigPath = Path.Combine(ConfigDir, "config.json");
        ErrorLogPath = Path.Combine(ConfigDir, "error.log");
    }

    public AppConfig Load()
    {
        try
        {
            if (!File.Exists(ConfigPath)) return new AppConfig();
            // UTF-8 with BOM tolerance, same reasoning as the Python build's load_config():
            // Notepad / PowerShell's own default UTF-8 writer both emit a BOM, and a naive
            // strict-UTF8 reader chokes on it - File.ReadAllText already strips a BOM for us.
            var text = File.ReadAllText(ConfigPath);
            return JsonSerializer.Deserialize<AppConfig>(text, JsonOpts) ?? new AppConfig();
        }
        catch
        {
            return new AppConfig();
        }
    }

    public void Save(AppConfig cfg)
    {
        var tmp = ConfigPath + ".tmp";
        File.WriteAllText(tmp, JsonSerializer.Serialize(cfg, JsonOpts));
        File.Copy(tmp, ConfigPath, overwrite: true);
        File.Delete(tmp);
    }

    public void Clear()
    {
        try { File.Delete(ConfigPath); } catch (FileNotFoundException) { }
    }
}
