using System.Globalization;
using System.Windows.Data;
using System.Windows.Media;

namespace MyWorkDay.Converters;

/// <summary>Timeline dot color per real activity kind - "worklog" (time you actually logged,
/// the core work signal) gets the same purple the Python build used for a git commit; "comment"
/// gets blue, the one activity signal git scanning could never see at all.</summary>
public sealed class ActivityKindToBrushConverter : IValueConverter
{
    private static readonly Brush Worklog = new SolidColorBrush(Color.FromRgb(0x7C, 0x3A, 0xED));
    private static readonly Brush Comment = new SolidColorBrush(Color.FromRgb(0x25, 0x63, 0xEB));

    public object Convert(object? value, Type targetType, object parameter, CultureInfo culture) => (value as string) switch
    {
        "comment" => Comment,
        _ => Worklog,
    };

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => throw new NotSupportedException();
}

/// <summary>Traffic-light coding for a team member's logged-time strength bar: red = barely
/// started (&lt;40% of a full day), orange = partway (&lt;80%), green = a full day's worth.</summary>
public sealed class StrengthPctToBrushConverter : IValueConverter
{
    private static readonly Brush Low = new SolidColorBrush(Color.FromRgb(0xDC, 0x26, 0x26));
    private static readonly Brush Mid = new SolidColorBrush(Color.FromRgb(0xEA, 0x58, 0x0C));
    private static readonly Brush High = new SolidColorBrush(Color.FromRgb(0x16, 0xA3, 0x4A));

    public object Convert(object? value, Type targetType, object parameter, CultureInfo culture)
    {
        var pct = value is int i ? i : 0;
        return pct < 40 ? Low : pct < 80 ? Mid : High;
    }

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => throw new NotSupportedException();
}
