using System.Globalization;
using System.Windows.Data;
using MyWorkDay.Core;

namespace MyWorkDay.Converters;

/// <summary>Turns a "yyyy-MM-dd" work-date key into a friendly label ("Wed, Sep 24, 2026") for
/// the date dropdown - same idea as the Python build's dateSelect option text, just applied to
/// every entry instead of only the selected one.</summary>
public sealed class DateKeyToLabelConverter : IValueConverter
{
    public object Convert(object? value, Type targetType, object parameter, CultureInfo culture)
    {
        if (value is not string s || string.IsNullOrWhiteSpace(s)) return value ?? "";
        try
        {
            return WorkDate.Parse(s).ToString("ddd, MMM d, yyyy");
        }
        catch
        {
            return s;
        }
    }

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => throw new NotSupportedException();
}
