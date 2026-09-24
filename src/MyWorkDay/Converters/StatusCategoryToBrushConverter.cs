using System.Globalization;
using System.Windows.Data;
using System.Windows.Media;

namespace MyWorkDay.Converters;

/// <summary>Maps Jira's own statusCategory.key ("new"/"indeterminate"/"done" - the same 3-way
/// split Jira's UI uses to color any status pill) to this app's status-pill background.</summary>
public sealed class StatusCategoryToBrushConverter : IValueConverter
{
    private static readonly Brush New = new SolidColorBrush(Color.FromRgb(0xE5, 0xE7, 0xEB));
    private static readonly Brush Indeterminate = new SolidColorBrush(Color.FromRgb(0xFE, 0xF3, 0xC7));
    private static readonly Brush Done = new SolidColorBrush(Color.FromRgb(0xDC, 0xFC, 0xE7));

    public object Convert(object? value, Type targetType, object parameter, CultureInfo culture) => (value as string) switch
    {
        "done" => Done,
        "indeterminate" => Indeterminate,
        _ => New,
    };

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => throw new NotSupportedException();
}

public sealed class StatusCategoryToForegroundConverter : IValueConverter
{
    private static readonly Brush New = new SolidColorBrush(Color.FromRgb(0x4B, 0x55, 0x63));
    private static readonly Brush Indeterminate = new SolidColorBrush(Color.FromRgb(0x92, 0x40, 0x0E));
    private static readonly Brush Done = new SolidColorBrush(Color.FromRgb(0x16, 0xA3, 0x4A));

    public object Convert(object? value, Type targetType, object parameter, CultureInfo culture) => (value as string) switch
    {
        "done" => Done,
        "indeterminate" => Indeterminate,
        _ => New,
    };

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => throw new NotSupportedException();
}

/// <summary>The small colored icon square on a ticket row - green check for a Done-category
/// ticket, purple bolt for anything still in progress (indeterminate or new), echoing the
/// Python build's merged-vs-in-progress ticon coloring with the closest Jira-only equivalent.</summary>
public sealed class StatusCategoryToIconBackgroundConverter : IValueConverter
{
    private static readonly Brush Done = new SolidColorBrush(Color.FromRgb(0xDC, 0xFC, 0xE7));
    private static readonly Brush InProgress = new SolidColorBrush(Color.FromRgb(0xED, 0xE9, 0xFE));

    public object Convert(object? value, Type targetType, object parameter, CultureInfo culture)
        => (value as string) == "done" ? Done : InProgress;

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => throw new NotSupportedException();
}

public sealed class StatusCategoryToIconForegroundConverter : IValueConverter
{
    private static readonly Brush Done = new SolidColorBrush(Color.FromRgb(0x16, 0xA3, 0x4A));
    private static readonly Brush InProgress = new SolidColorBrush(Color.FromRgb(0x7C, 0x3A, 0xED));

    public object Convert(object? value, Type targetType, object parameter, CultureInfo culture)
        => (value as string) == "done" ? Done : InProgress;

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => throw new NotSupportedException();
}

public sealed class StatusCategoryToIconGlyphConverter : IValueConverter
{
    public object Convert(object? value, Type targetType, object parameter, CultureInfo culture)
        => (value as string) == "done" ? "✓" : "⚡";

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => throw new NotSupportedException();
}
