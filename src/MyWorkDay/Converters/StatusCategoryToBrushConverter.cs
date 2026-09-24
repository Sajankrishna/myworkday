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
