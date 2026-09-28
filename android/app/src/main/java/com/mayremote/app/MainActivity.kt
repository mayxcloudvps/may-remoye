package com.mayremote.app

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.media.MediaCodec
import android.media.MediaFormat
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.MotionEvent
import android.view.Surface
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.Executors

/**
 * Điều khiển:
 *  - Chạm = chuột trái (giữ + kéo được), chạm 2 ngón = chuột phải
 *  - Nút ⌨  = bàn phím ảo (phím game: WASD, Shift, Ctrl, Esc, mũi tên...). Shift/Ctrl/Alt bấm 1 lần = giữ, bấm lại = nhả
 *  - Nút Aa = ô nhập chữ (dùng bàn phím điện thoại, gõ tiếng Việt OK) -> Gửi để gõ vào PC
 * Giao thức: [1 byte type][4 byte len][payload] - xem server.py
 */
class MainActivity : Activity(), SurfaceHolder.Callback {

    private lateinit var view: SurfaceView
    private lateinit var kbPanel: LinearLayout
    private lateinit var textBar: LinearLayout
    private lateinit var textInput: EditText

    private var surface: Surface? = null
    private var host = ""
    private var port = 5900
    private var token = ""

    @Volatile private var running = false
    private var socket: Socket? = null
    private var out: DataOutputStream? = null
    private val sender = Executors.newSingleThreadExecutor()
    private val heldMods = HashSet<Int>()

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    @Suppress("DEPRECATION")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        view = SurfaceView(this)
        view.holder.addCallback(this)
        view.setOnTouchListener { _, e -> onTouch(e); true }

        val root = FrameLayout(this)
        root.addView(view, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
        buildOverlays(root)
        setContentView(root)

        root.systemUiVisibility = (View.SYSTEM_UI_FLAG_FULLSCREEN
                or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY)
        showConnectDialog()
    }

    // ------------------------------------------------------------ overlays
    private class K(val label: String, val vk: Int, val w: Float = 1f, val mod: Boolean = false)

    private fun letters(s: String) = s.map { K(it.toString(), it.code) }

    private val keyRows: List<List<K>> by lazy {
        listOf(
            listOf(K("Esc", 0x1B, 1.4f)) + letters("1234567890") +
                    listOf(K("-", 0xBD), K("=", 0xBB), K("⌫", 0x08, 1.6f)),
            listOf(K("Tab", 0x09, 1.5f)) + letters("QWERTYUIOP") +
                    listOf(K("[", 0xDB), K("]", 0xDD), K("\\", 0xDC)),
            listOf(K("Caps", 0x14, 1.8f)) + letters("ASDFGHJKL") +
                    listOf(K(";", 0xBA), K("'", 0xDE), K("Enter", 0x0D, 2f)),
            listOf(K("Shift", 0xA0, 2.3f, true)) + letters("ZXCVBNM") +
                    listOf(K(",", 0xBC), K(".", 0xBE), K("/", 0xBF), K("Shift", 0xA1, 2.3f, true)),
            listOf(
                K("Ctrl", 0xA2, 1.5f, true), K("Win", 0x5B, 1.3f), K("Alt", 0xA4, 1.3f, true),
                K("Space", 0x20, 6f), K("←", 0x25), K("↑", 0x26), K("↓", 0x28), K("→", 0x27)
            )
        )
    }

    private fun buildOverlays(root: FrameLayout) {
        kbPanel = buildKeyboard()
        root.addView(kbPanel, FrameLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT, Gravity.BOTTOM))

        textBar = buildTextBar()
        root.addView(
            textBar,
            FrameLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT, Gravity.TOP).apply { rightMargin = dp(100) }
        )

        val toggles = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        toggles.addView(toggleButton("⌨") {
            kbPanel.visibility = if (kbPanel.visibility == View.VISIBLE) View.GONE else View.VISIBLE
        })
        toggles.addView(toggleButton("Aa") { toggleTextBar() })
        root.addView(toggles, FrameLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT, Gravity.TOP or Gravity.END))
    }

    private fun toggleButton(label: String, onClick: () -> Unit) = TextView(this).apply {
        text = label
        textSize = 16f
        gravity = Gravity.CENTER
        setTextColor(0xFFFFFFFF.toInt())
        setBackgroundColor(0x99000000.toInt())
        layoutParams = LinearLayout.LayoutParams(dp(48), dp(36)).apply { setMargins(dp(2), dp(2), dp(2), 0) }
        setOnClickListener { onClick() }
    }

    private fun buildKeyboard(): LinearLayout {
        val panel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(0xCC101010.toInt())
            setPadding(dp(4), dp(4), dp(4), dp(4))
            visibility = View.GONE
            isClickable = true   // chặn chạm xuyên xuống game
        }
        val normal = 0xFF3A3A3A.toInt()
        val pressed = 0xFF777777.toInt()
        val latched = 0xFF2E7D32.toInt()

        for (row in keyRows) {
            val r = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
            for (k in row) {
                val tv = TextView(this).apply {
                    text = k.label
                    textSize = 12f
                    gravity = Gravity.CENTER
                    setTextColor(0xFFFFFFFF.toInt())
                    setBackgroundColor(normal)
                }
                tv.setOnTouchListener { v, e ->
                    if (k.mod) {
                        // Shift/Ctrl/Alt: bấm để giữ, bấm lại để nhả
                        if (e.actionMasked == MotionEvent.ACTION_DOWN) {
                            val on = heldMods.add(k.vk)
                            if (!on) heldMods.remove(k.vk)
                            key(k.vk, on)
                            v.setBackgroundColor(if (on) latched else normal)
                        }
                    } else {
                        when (e.actionMasked) {
                            MotionEvent.ACTION_DOWN -> { key(k.vk, true); v.setBackgroundColor(pressed) }
                            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> { key(k.vk, false); v.setBackgroundColor(normal) }
                        }
                    }
                    true
                }
                r.addView(tv, LinearLayout.LayoutParams(0, dp(34), k.w).apply { setMargins(dp(1), dp(1), dp(1), dp(1)) })
            }
            panel.addView(r, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        }
        return panel
    }

    private fun buildTextBar(): LinearLayout {
        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setBackgroundColor(0xCC101010.toInt())
            visibility = View.GONE
            isClickable = true
        }
        textInput = EditText(this).apply {
            hint = "Nhập chữ (tiếng Việt OK)"
            setHintTextColor(0xFF999999.toInt())
            setTextColor(0xFFFFFFFF.toInt())
            setSingleLine()
            imeOptions = EditorInfo.IME_ACTION_SEND or EditorInfo.IME_FLAG_NO_EXTRACT_UI
        }
        fun submit() {
            val s = textInput.text.toString()
            if (s.isNotEmpty()) {
                sendInput(JSONObject().put("t", "text").put("s", s))
                textInput.setText("")
            }
        }
        textInput.setOnEditorActionListener { _, _, _ -> submit(); true }
        val send = Button(this).apply { text = "Gửi"; setOnClickListener { submit() } }
        val enter = Button(this).apply {
            text = "⏎"
            setOnClickListener { key(0x0D, true); key(0x0D, false) }
        }
        bar.addView(textInput, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        bar.addView(send, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT))
        bar.addView(enter, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT))
        return bar
    }

    private fun toggleTextBar() {
        val show = textBar.visibility != View.VISIBLE
        textBar.visibility = if (show) View.VISIBLE else View.GONE
        val imm = getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
        if (show) {
            textInput.requestFocus()
            imm.showSoftInput(textInput, InputMethodManager.SHOW_IMPLICIT)
        } else {
            imm.hideSoftInputFromWindow(textInput.windowToken, 0)
        }
    }

    // ------------------------------------------------------------ connect dialog
    private fun showConnectDialog() {
        val prefs = getSharedPreferences("cfg", MODE_PRIVATE)
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 24, 48, 0)
        }
        val eHost = EditText(this).apply { hint = "IP máy tính"; setText(prefs.getString("host", "")) }
        val ePort = EditText(this).apply {
            hint = "Port"; inputType = InputType.TYPE_CLASS_NUMBER
            setText(prefs.getString("port", "5900"))
        }
        val eTok = EditText(this).apply { hint = "Token"; setText(prefs.getString("token", "")) }
        box.addView(eHost); box.addView(ePort); box.addView(eTok)

        AlertDialog.Builder(this)
            .setTitle("mây remote")
            .setView(box)
            .setCancelable(false)
            .setPositiveButton("Connect") { _, _ ->
                host = eHost.text.toString().trim()
                port = ePort.text.toString().toIntOrNull() ?: 5900
                token = eTok.text.toString()
                prefs.edit().putString("host", host).putString("port", port.toString())
                    .putString("token", token).apply()
                startIfReady()
            }
            .show()
    }

    // ------------------------------------------------------------ surface
    override fun surfaceCreated(h: SurfaceHolder) { surface = h.surface; startIfReady() }
    override fun surfaceChanged(h: SurfaceHolder, f: Int, w: Int, hh: Int) {}
    override fun surfaceDestroyed(h: SurfaceHolder) { running = false; surface = null }

    override fun onDestroy() {
        running = false
        try { socket?.close() } catch (_: Exception) {}
        super.onDestroy()
    }

    private fun startIfReady() {
        if (host.isNotEmpty() && surface != null && !running) {
            running = true
            Thread { runSession() }.start()
        }
    }

    // ------------------------------------------------------------ network
    private fun readMsg(din: DataInputStream): Pair<Int, ByteArray> {
        val t = din.readUnsignedByte()
        val len = din.readInt()
        val b = ByteArray(len)
        din.readFully(b)
        return Pair(t, b)
    }

    private fun sendMsg(t: Int, payload: ByteArray) {
        sender.execute {
            try {
                synchronized(this) {
                    out?.let {
                        it.writeByte(t); it.writeInt(payload.size); it.write(payload); it.flush()
                    }
                }
            } catch (_: Exception) {}
        }
    }

    private fun sendInput(j: JSONObject) = sendMsg(2, j.toString().toByteArray())

    private fun makeTrack(rate: Int): AudioTrack {
        val minBuf = AudioTrack.getMinBufferSize(rate, AudioFormat.CHANNEL_OUT_STEREO, AudioFormat.ENCODING_PCM_16BIT)
        val size = maxOf(minBuf, rate * 4 / 10)   // ~100ms
        return AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_GAME)
                    .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build()
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(rate)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_STEREO).build()
            )
            .setBufferSizeInBytes(size)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY)
            .build()
    }

    private fun runSession() {
        var codec: MediaCodec? = null
        var track: AudioTrack? = null
        try {
            val s = Socket()
            s.tcpNoDelay = true
            s.connect(InetSocketAddress(host, port), 5000)
            socket = s
            val din = DataInputStream(BufferedInputStream(s.getInputStream(), 1 shl 16))
            out = DataOutputStream(s.getOutputStream())

            sendMsg(3, JSONObject().put("token", token).toString().toByteArray())
            val (ht, hp) = readMsg(din)
            if (ht != 0) throw IllegalStateException("Sai phản hồi từ server")
            val hello = JSONObject(String(hp))

            val fmt = MediaFormat.createVideoFormat("video/avc", hello.getInt("w"), hello.getInt("h"))
            if (Build.VERSION.SDK_INT >= 30) fmt.setInteger(MediaFormat.KEY_LOW_LATENCY, 1)
            codec = MediaCodec.createDecoderByType("video/avc")
            codec.configure(fmt, surface, null, 0)
            codec.start()
            val info = MediaCodec.BufferInfo()

            val au = hello.optJSONObject("audio")
            if (au != null) {
                track = makeTrack(au.getInt("rate"))
                track.play()
            }

            while (running) {
                val (type, data) = readMsg(din)
                when (type) {
                    1 -> {
                        val i = codec.dequeueInputBuffer(20_000)
                        if (i >= 0) {
                            val buf = codec.getInputBuffer(i)!!
                            buf.clear()
                            buf.put(data)
                            codec.queueInputBuffer(i, 0, data.size, System.nanoTime() / 1000, 0)
                        }
                        while (true) {
                            val o = codec.dequeueOutputBuffer(info, 0)
                            if (o >= 0) codec.releaseOutputBuffer(o, true) else break
                        }
                    }
                    4 -> track?.write(data, 0, data.size, AudioTrack.WRITE_NON_BLOCKING)
                }
            }
        } catch (e: Exception) {
            runOnUiThread { Toast.makeText(this, "Lỗi: ${e.message}", Toast.LENGTH_LONG).show() }
        } finally {
            running = false
            try { codec?.stop(); codec?.release() } catch (_: Exception) {}
            try { track?.stop(); track?.release() } catch (_: Exception) {}
            try { socket?.close() } catch (_: Exception) {}
            out = null
            heldMods.clear()
            runOnUiThread { if (!isFinishing) showConnectDialog() }
        }
    }

    // ------------------------------------------------------------ input
    private fun key(vk: Int, down: Boolean) = sendInput(
        JSONObject().put("t", "k").put("vk", vk).put("d", if (down) 1 else 0)
    )

    private fun move(x: Float, y: Float) = sendInput(
        JSONObject().put("t", "mm")
            .put("x", x.coerceIn(0f, 1f).toDouble())
            .put("y", y.coerceIn(0f, 1f).toDouble())
    )

    private fun button(b: String, down: Boolean) = sendInput(
        JSONObject().put("t", "mb").put("b", b).put("d", if (down) 1 else 0)
    )

    private fun onTouch(e: MotionEvent) {
        val w = view.width.toFloat()
        val h = view.height.toFloat()
        when (e.actionMasked) {
            MotionEvent.ACTION_DOWN -> { move(e.x / w, e.y / h); button("l", true) }
            MotionEvent.ACTION_MOVE -> move(e.x / w, e.y / h)
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> button("l", false)
            MotionEvent.ACTION_POINTER_DOWN -> {   // ngón thứ 2 = chuột phải
                button("l", false); button("r", true); button("r", false)
            }
        }
    }
}
