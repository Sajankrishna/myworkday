using System.Globalization;
using System.Windows.Data;
using System.Windows.Media;
using MyWorkDay.Controls;

namespace MyWorkDay.Converters;

/// <summary>Maps an ItemsControl.AlternationIndex to the same donut-chart palette DonutChart
/// itself cycles through, so a legend swatch always matches its wedge's color.</summary>
public sealed class IndexToPaletteBrushConverter : IValueConverter
{
    public object Convert(object? value, Type targetType, object parameter, CultureInfo culture)
    {
        var index = value is int i ? i : 0;
        var color = DonutChart.Palette[((index % DonutChart.Palette.Length) + DonutChart.Palette.Length) % DonutChart.Palette.Length];
        return new SolidColorBrush(color);
    }

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => throw new NotSupportedException();
}
