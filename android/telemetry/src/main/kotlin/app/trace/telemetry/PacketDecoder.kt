package app.trace.telemetry

import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

typealias Packet = Map<String, Any?>

/** Port of udp._decrypt / udp._parse. Stateful exactly like the Python module. */
class PacketDecoder {
    var track: String = "unknown_track"
    var car: String = "unknown_car"

    private var prevX: Double? = null
    private var prevZ: Double? = null
    private var prevHeading: Double? = null
    private var cumDist = 0.0
    private var prevLap: Int? = null
    private val steerLock = HashMap<Pair<Int, String>, Double>()
    private var steerCorr = 0.0

    fun resetLap() { prevX = null; prevZ = null; prevHeading = null; cumDist = 0.0 }

    fun decrypt(data: ByteArray): ByteArray? {
        if (data.size < 0x44) return null
        return try {
            val bb = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN)
            val seed = bb.getInt(0x40)
            val xorConst = when (data.size) {
                344 -> 0x55FABB4F.toInt()
                296 -> 0xDEADBEAF.toInt()
                else -> 0xDEADBEEF.toInt()
            }
            val iv = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN)
                .putInt(seed xor xorConst).putInt(seed).array()
            val dec = Salsa20.crypt(data, KEY, iv)
            if (ByteBuffer.wrap(dec).order(ByteOrder.LITTLE_ENDIAN).getInt(0) != 0x47375330) null else dec
        } catch (e: Exception) { null }
    }

    fun decode(raw: ByteArray): Packet? = decrypt(raw)?.let { parse(it) }

    fun parse(data: ByteArray): Packet? = try { parseInner(data) } catch (e: Exception) { null }

    private fun parseInner(data: ByteArray): Packet {
        val bb = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN)
        fun f(o: Int) = bb.getFloat(o).toDouble()
        fun i(o: Int) = bb.getInt(o)
        fun h(o: Int) = bb.getShort(o).toInt()
        fun hu(o: Int) = bb.getShort(o).toInt() and 0xFFFF
        fun b(o: Int) = data[o].toInt() and 0xFF

        val wx = f(0x04); val wy = f(0x08); val wz = f(0x0C)
        val vx = f(0x10); val vy = f(0x14); val vz = f(0x18)
        val qx = f(0x1C); val qy = f(0x20); val qz = f(0x24); val qw = f(0x28)
        val heading = atan2(2.0 * (qw * qy + qz * qx), 1.0 - 2.0 * (qx * qx + qy * qy))
        val angX = f(0x2C); val angY = f(0x30); val angZ = f(0x34)

        var steering = 0.0
        prevHeading?.let { ph ->
            var dh = heading - ph
            while (dh > Math.PI) dh -= 2 * Math.PI
            while (dh < -Math.PI) dh += 2 * Math.PI
            steering = max(-1.0, min(1.0, dh / 0.2))
        }
        prevHeading = heading
        var steeringSource = "heading"

        val speedKmh = f(0x4C) * 3.6

        val currentLap = h(0x74)
        if (prevLap != null && currentLap != prevLap) cumDist = 0.0
        prevLap = currentLap

        val px = prevX; val pz = prevZ
        if (px != null && pz != null && speedKmh > 2.0) {
            val dx = wx - px; val dz = wz - pz
            cumDist += sqrt(dx * dx + dz * dz)
        }
        prevX = wx; prevZ = wz

        val gearByte = b(0x90)
        val gear = gearByte and 0x0F
        var suggested = gearByte shr 4
        if (suggested == 0x0F) suggested = 0

        val fuel = f(0x44); val cap = f(0x48)
        val diam = doubleArrayOf(f(0xB4), f(0xB8), f(0xBC), f(0xC0))
        val rot = doubleArrayOf(abs(f(0xA4)), abs(f(0xA8)), abs(f(0xAC)), abs(f(0xB0)))
        val slip = DoubleArray(4) {
            val spd = 3.6 * diam[it] * rot[it]
            if (speedKmh > 1) spd / speedKmh else 1.0
        }

        val bestLap = i(0x78); val lastLap = i(0x7C)
        val currentPos = h(0x84); val totalPos = h(0x86)
        val rpmLimiter = hu(0x8A)

        val len = data.size
        var wheelRotation: Double? = null; var angVel: Double? = null
        var sway: Double? = null; var heave: Double? = null; var surge: Double? = null
        var torque: List<Double>? = null; var energy: Double? = null
        var thrF: Double? = null; var brkF: Double? = null
        var surface: List<String>? = null; var curLapMs: Int? = null
        var wheelAngle: List<Double>? = null; var wheelBase: Double? = null; var category: String? = null
        if (len >= 316) { wheelRotation = f(0x128); angVel = f(0x12C); sway = f(0x130); heave = f(0x134); surge = f(0x138) }
        if (len >= 344) {
            thrF = b(0x13C) / 255.0; brkF = b(0x13D) / 255.0
            torque = List(4) { f(0x140 + it * 4) }; energy = f(0x150)
        }
        if (len >= 368) {
            surface = List(4) { if (b(0x158 + it) != 0) b(0x158 + it).toChar().toString() else "" }
            curLapMs = i(0x15C); wheelAngle = listOf(f(0x160), f(0x164)); wheelBase = f(0x168)
            category = (0 until 4).filter { b(0x16C + it) != 0 }.joinToString("") { b(0x16C + it).toChar().toString() }
        }

        val carId = i(0x124)
        var real: Double? = null; var src: String? = null
        if (wheelAngle != null) { real = (wheelAngle[0] + wheelAngle[1]) / 2.0; src = "wheel" }
        else if (wheelRotation != null) { real = wheelRotation; src = "rotation" }
        if (real != null && real.isFinite()) {
            normaliseSteering(real, src!!, steering, carId, speedKmh)?.let { steering = it; steeringSource = src }
        }

        val f8e = b(0x8E); val f8f = b(0x8F); val f93 = b(0x93)
        val fl = f8e or (f8f shl 8)
        fun bit(n: Int) = (fl and (1 shl n)) != 0

        return linkedMapOf(
            "track_name" to track, "car_name" to car, "packet_id" to i(0x70),
            "lap_number" to currentLap, "track_position" to cumDist,
            "world_x" to wx, "world_y" to wy, "world_z" to wz, "heading" to heading,
            "speed_kmh" to speedKmh, "throttle" to b(0x91) / 255.0, "brake" to b(0x92) / 255.0,
            "steering" to steering, "steering_source" to steeringSource,
            "gear" to gear, "suggested_gear" to suggested, "rpm" to f(0x3C),
            "max_rpm" to rpmLimiter, "rpm_warning" to hu(0x88), "rpm_limiter" to rpmLimiter,
            "tyre_temp_fl" to f(0x60), "tyre_temp_fr" to f(0x64), "tyre_temp_rl" to f(0x68), "tyre_temp_rr" to f(0x6C),
            "tyre_slip_fl" to slip[0], "tyre_slip_fr" to slip[1], "tyre_slip_rl" to slip[2], "tyre_slip_rr" to slip[3],
            "susp_fl" to f(0xC4), "susp_fr" to f(0xC8), "susp_rl" to f(0xCC), "susp_rr" to f(0xD0),
            "vel_x" to vx, "vel_y" to vy, "vel_z" to vz, "ang_x" to angX, "ang_y" to angY, "ang_z" to angZ,
            "fuel_remaining" to fuel, "fuel_capacity" to cap, "is_ev" to (cap == 0.0),
            "boost" to f(0x50) - 1.0, "oil_temp" to f(0x5C), "water_temp" to f(0x58),
            "oil_pressure" to f(0x54), "ride_height_mm" to f(0x38) * 1000,
            "clutch" to f(0xF4), "clutch_engaged" to f(0xF8), "rpm_after_clutch" to f(0xFC),
            "gear_ratios" to List(8) { f(0x104 + it * 4) }, "transmission_top_speed" to f(0x100),
            "total_laps" to h(0x76), "current_position" to currentPos, "total_positions" to totalPos,
            "grid_position" to (if (currentPos > 0) currentPos else null),
            "grid_cars" to (if (totalPos > 0) totalPos else null),
            "best_lap_ms" to bestLap, "last_lap_ms" to lastLap, "in_pit" to false,
            "car_on_track" to bit(0), "paused" to bit(1), "loading" to bit(2), "in_gear" to bit(3),
            "has_turbo" to bit(4), "rev_limit_alert" to bit(5), "handbrake_active" to bit(6),
            "lights_active" to bit(7), "high_beams" to bit(8), "low_beams" to bit(9),
            "asm_active" to bit(10), "tcs_active" to bit(11),
            "car_id" to carId, "flags_8e" to f8e, "flags_8f" to f8f, "flags_93" to f93,
            "time_of_day" to i(0x80), "est_top_speed" to h(0x8C),
            "wheel_rotation" to wheelRotation, "steering_angular_velocity" to angVel,
            "sway" to sway, "heave" to heave, "surge" to surge,
            "torque_vectors" to torque, "energy_recovery" to energy,
            "throttle_filtered" to thrF, "brake_filtered" to brkF,
            "surface_type" to surface, "current_lap_ms" to curLapMs,
            "wheel_steering_angle" to wheelAngle, "wheel_base" to wheelBase, "car_category" to category,
        )
    }

    private fun normaliseSteering(angle: Double, source: String, headingEstimate: Double, carId: Int, speedKmh: Double): Double? {
        val key = carId to source
        val floor = if (source == "wheel") 0.35 else 1.5
        val lock = max(max(steerLock[key] ?: 0.0, abs(angle)), floor)
        steerLock[key] = lock
        if (speedKmh > 30.0 && abs(headingEstimate) > 0.005 && abs(angle) > 0.02) steerCorr += angle * headingEstimate
        if (steerCorr == 0.0) return null
        val sign = if (steerCorr < 0) -1.0 else 1.0
        return max(-1.0, min(1.0, sign * angle / lock))
    }

    companion object {
        val KEY: ByteArray = "Simulator Interface Packet GT7 ver 0.0".toByteArray(Charsets.US_ASCII).copyOf(32)
    }
}
