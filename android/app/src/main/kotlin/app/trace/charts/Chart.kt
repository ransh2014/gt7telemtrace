package app.trace.charts

import androidx.compose.ui.graphics.Color

/** A tiny declarative stand-in for the matplotlib axes the PC analysts draw into. */

enum class Step { NONE, POST, MID }

sealed interface Layer

class LineL(
    val x: DoubleArray, val y: DoubleArray, val color: Color, val lw: Float = 1.5f, val alpha: Float = 1f,
    val dash: Int = 0,                  // 0 solid, 1 dashed, 2 dotted
    val step: Step = Step.NONE, val label: String? = null,
    val marker: Float = 0f, val markerSquare: Boolean = false,
) : Layer

class FillL(
    val x: DoubleArray, val y1: DoubleArray, val y2: DoubleArray, val color: Color, val alpha: Float,
    val step: Step = Step.NONE, val where: BooleanArray? = null,
) : Layer

class ScatterL(
    val x: DoubleArray, val y: DoubleArray, val color: Color = Color.White,
    val c: DoubleArray? = null, val cmap: String? = null, val vmin: Double? = null, val vmax: Double? = null,
    val s: Float = 2f, val alpha: Float = 1f, val label: String? = null, val z: Int = 1,
) : Layer

class HistL(
    val values: DoubleArray, val bins: Int, val color: Color, val alpha: Float, val edges: DoubleArray? = null,
    val outline: Boolean = false, val lw: Float = 1.5f, val density: Boolean = false,
    val rwidth: Float = 1f, val label: String? = null,
) : Layer

/** Vertical bars (pos = x centres, len = heights) or horizontal when [horizontal] (pos = y centres, len = widths). */
class BarL(
    val pos: DoubleArray, val len: DoubleArray, val thick: DoubleArray, val colors: List<Color>, val alpha: Float = 1f,
    val horizontal: Boolean = false, val label: String? = null, val hatch: Boolean = false,
) : Layer

class HLineL(val y: Double, val color: Color, val lw: Float = 0.8f, val dash: Int = 1, val alpha: Float = 1f) : Layer
class VLineL(val x: Double, val color: Color, val lw: Float = 0.8f, val dash: Int = 1, val alpha: Float = 1f) : Layer
class HSpanL(val y0: Double, val y1: Double, val color: Color, val alpha: Float) : Layer

/** [axes] = true puts (x, y) in 0..1 axes coordinates; ha 0 left / .5 centre / 1 right; va 0 bottom / .5 centre / 1 top. */
class TextL(
    val x: Double, val y: Double, val text: String, val color: Color, val size: Float = 8f,
    val ha: Float = 0.5f, val va: Float = 0.5f, val axes: Boolean = false, val bold: Boolean = false,
    val bbox: Color? = null, val clip: Boolean = false,
) : Layer

class Cb(val cmap: String, val vmin: Double, val vmax: Double)

class Tick(val at: Double, val text: String)

sealed interface Panel {
    val title: String?
    val titleColor: Color?
    val aspect: Float            // height / width of the panel on screen
    val titleSize: Float
}

/** A cartesian axes. */
class XY(
    override val title: String? = null,
    val xlabel: String? = null, val ylabel: String? = null,
    val layers: List<Layer> = emptyList(),
    val twin: List<Layer> = emptyList(), val twinLabel: String? = null, val twinColor: Color? = null,
    val grid: Boolean = true, val gridAxis: Char = 'b',
    val xlim: DoubleArray? = null, val ylim: DoubleArray? = null,
    val xticks: List<Tick>? = null, val yticks: List<Tick>? = null,
    val legend: Boolean = false, val legendCols: Int = 1, val legendSize: Float = 7f, val legendBelow: Boolean = false,
    val cb: Cb? = null, val ylabelColor: Color? = null,
    val off: Boolean = false, val noXTicks: Boolean = false, val noYTicks: Boolean = false, val noFrame: Boolean = false,
    val xTickSize: Float = 7f,
    override val titleColor: Color? = null, override val aspect: Float = 0.62f, override val titleSize: Float = 9f,
) : Panel

/** A coloured track line (matplotlib LineCollection) or several fixed-colour polylines. */
class MapP(
    override val title: String?, val x: DoubleArray, val z: DoubleArray,
    val v: DoubleArray?, val cmap: String? = null, val lw: Float = 2.2f, val alpha: Float = 0.9f,
    val lines: List<MapLine> = emptyList(), val labels: List<MapLabel> = emptyList(),
    override val titleColor: Color? = null, override val aspect: Float = 0.9f, override val titleSize: Float = 9f,
) : Panel

class MapLine(val x: DoubleArray, val z: DoubleArray, val color: Color, val lw: Float, val alpha: Float)
class MapLabel(val x: Double, val z: Double, val text: String, val bg: Color)

class RadarS(val values: DoubleArray, val color: Color, val dash: Boolean, val fillAlpha: Float)

class Radar(
    override val title: String?, val labels: List<String>, val series: List<RadarS>,
    override val titleColor: Color? = null, override val aspect: Float = 0.9f, override val titleSize: Float = 9f,
) : Panel

/** Text only (axis off). Lines are drawn top-left in mono type. */
class TextP(
    override val title: String?, val lines: List<String>, val color: Color, val bg: Boolean = true,
    val size: Float = 8f, val center: Boolean = false,
    override val titleColor: Color? = null, override val aspect: Float = 0f, override val titleSize: Float = 9f,
) : Panel

/** imshow of a matrix; NaN cells are left blank. */
class Heat(
    override val title: String?, val values: Array<DoubleArray>, val cmap: String, val vmin: Double, val vmax: Double,
    val cellText: Array<Array<String?>>? = null, val cellColor: Color = Color.White, val cellSize: Float = 6f,
    val xLabels: List<String>? = null, val yLabels: List<String>? = null, val cb: Boolean = true,
    override val titleColor: Color? = null, override val aspect: Float = 0.55f, override val titleSize: Float = 9f,
) : Panel

/** A group of panels laid out on a grid, like one matplotlib figure. */
class Fig(
    val panels: List<Panel>, val cols: Int, val suptitle: String? = null,
    val footer: String? = null,
)

fun full(n: Int, v: Double) = DoubleArray(n) { v }
