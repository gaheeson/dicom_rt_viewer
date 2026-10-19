#!/usr/bin/env python3
"""
dicom_rt_viewer.py — DICOM RT Viewer  (RT Record / RT Plan / RT Dose)

  python dicom_rt_viewer.py                # 실행 후 [Open] 으로 파일 선택
  python dicom_rt_viewer.py file.dcm       # 파일을 바로 열기

─── 공통 ──────────────────────────────────────────────────────────────────────
- 왼쪽: 태그 트리 (Sequence 클릭으로 펼침/접기)
- 오른쪽 Map 탭: Spot map / Dose map 시각화
- 오른쪽 Summary 탭: Modality에 맞는 요약 정보
- Hide PHI: 환자 이름 · ID · 생년월일 등을 가림
- Search: 태그 이름 / 번호 / 값으로 찾기 (Enter = 다음)

─── RT Record / RT Plan ───────────────────────────────────────────────────────
- Spot map 탭: Control point 선택 시 스팟 조사 경로 시각화
  (Beam 이름, 에너지, spot 수, fixed-coordinate spot 표시)
- Overlay…: 같은 빔의 Plan/Record를 동시에 표시
  (Plan = 파란 빈 원, Record = 빨간 채운 원, 편차 화살표 옵션)
- ← → / ↑ ↓: 이전/다음 에너지 레이어 이동

─── RT Dose ──────────────────────────────────────────────────────────────────
- Dose map 탭: Axial / Coronal / Sagittal 슬라이스 뷰어
  (슬라이더로 슬라이스 이동, Gy 단위 컬러맵, 통계 표시)
- W/L 슬라이더: Min / Max dose 표시 범위 조절
- ← → / ↑ ↓: 이전/다음 슬라이스 이동

─── 단축키 ───────────────────────────────────────────────────────────────────
  Ctrl+O        파일 열기
  Ctrl+Shift+O  Overlay 파일 열기
  Ctrl+S        현재 그림 저장
  ← →          RTDOSE: 슬라이스  /  RTPLAN·RTRECORD: 이전·다음 레이어
  ↑ ↓          (동일)

Requires: pydicom, numpy, matplotlib, tkinter
  Mac: python.org 설치본에는 tkinter 포함. Homebrew python이면 `brew install python-tk`
"""
import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
import pydicom
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

# ── PHI 태그 목록 ──────────────────────────────────────────────────────────────
PHI = {(0x0010, 0x0010), (0x0010, 0x0020), (0x0010, 0x0030), (0x0010, 0x1000),
       (0x0010, 0x1001), (0x0008, 0x0090), (0x0008, 0x1070), (0x0008, 0x0050),
       (0x0010, 0x21B0)}

FIXED_LIMIT = -100.0
IMPAC_GUESS = {0x1004: "temperature? (°C)", 0x1005: "pressure? (hPa)",
               0x1006: "correction factor?", 0x1019: "= Delivered Primary Meterset"}

# SOP Class UID → modality 이름
SOP_NAMES = {
    "1.2.840.10008.5.1.4.1.1.481.2": "RTDOSE",
    "1.2.840.10008.5.1.4.1.1.481.5": "RTPLAN",
    "1.2.840.10008.5.1.4.1.1.481.8": "RTPLAN",   # RT Ion Plan
    "1.2.840.10008.5.1.4.1.1.481.9": "RTRECORD",  # RT Ion Beams Treatment Record
    "1.2.840.10008.5.1.4.1.1.481.4": "RTRECORD",  # RT Beams Treatment Record
}


# ── 유틸 ───────────────────────────────────────────────────────────────────────
def as_text(v):
    if isinstance(v, (bytes, bytearray)):
        s = bytes(v).decode("ascii", "replace").strip("\x00 ").strip()
        printable = sum(ch.isprintable() for ch in s)
        return s if s and printable / len(s) > 0.9 else f"<{len(v)} bytes>"
    return str(v)


def a1(v):
    return np.atleast_1d(np.array(v, dtype=float))


def detect_modality(ds):
    """SOP Class UID 또는 Modality 태그로 RT modality 감지."""
    sop = str(ds.get("SOPClassUID", ""))
    if sop in SOP_NAMES:
        return SOP_NAMES[sop]
    return str(ds.get("Modality", "UNKNOWN")).upper()


# ── 메인 앱 ────────────────────────────────────────────────────────────────────
# ── Tooltip helper ────────────────────────────────────────────────────────────
class _Tooltip:
    """Widget hover tooltip — text_fn() is called each time to get current text."""
    def __init__(self, widget, text_fn):
        self._w   = widget
        self._fn  = text_fn
        self._tip = None
        widget.bind("<Enter>",       self._show)
        widget.bind("<Leave>",       self._hide)
        widget.bind("<ButtonPress>", self._hide)

    def _show(self, _=None):
        text = self._fn()
        if not text:
            return
        x = self._w.winfo_rootx()
        y = self._w.winfo_rooty() + self._w.winfo_height() + 4
        self._tip = tw = tk.Toplevel(self._w)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=text, justify="left",
                 background="#FFFDE7", relief="solid", borderwidth=1,
                 font=("TkDefaultFont", 10), padx=8, pady=5).pack()

    def _hide(self, _=None):
        if self._tip:
            self._tip.destroy()
            self._tip = None


class App(tk.Tk):
    def __init__(self, path=None):
        super().__init__()
        self.title("DICOM RT Viewer")
        self.geometry("1400x880")
        self.ds = None
        self.path = None
        self.modality = None
        self.nodes = {}          # tree iid → (element or dataset, context)
        self.hide_phi = tk.BooleanVar(value=True)
        self._dose_im = None     # RTDOSE 컬러바 핸들
        self._dose_cbar = None
        self._search_hits = []
        self._search_pos = 0
        self._last_q = None

        # ── Overlay state ──
        self.ds2 = None
        self.path2 = None
        self.modality2 = None
        self._show_deviations = tk.BooleanVar(value=True)
        self._show_primary_spots  = tk.BooleanVar(value=True)
        self._show_overlay_spots  = tk.BooleanVar(value=True)

        # ── W/L state (RTDOSE windowing) ──
        self.dose_vmin = tk.DoubleVar(value=0.0)
        self.dose_vmax = tk.DoubleVar(value=1.0)
        self._dose_vmax_abs = 1.0

        # ── Current CP context (keyboard nav) ──
        self._cur_cp_seq = None
        self._cur_cp_k = 0
        self._cur_beam_num = None

        # ── Spot map display config ──
        self._spot_cfg = {
            "single_color":  "#23292F",
            "show_first":    True,
            "first_label":   "first",
            "first_color":   "#2E90FA",
            "show_last":     True,
            "last_label":    "last",
            "last_color":    "#F79009",
            "path_color":    "#98A2B3",
            "plan_label":    "Plan",
            "plan_color":    "#1570EF",
            "record_label":  "Record",
            "record_color":  "#D92D20",
            "arrow_color":   "#F79009",
        }

        # ── Tree source toggle ──
        self._tree_source = "primary"  # "primary" | "overlay"
        self._right_visible = False   # right panel starts hidden
        self._tree_expanded = False    # expand/collapse toggle

        # ── Hover tooltip / scroll state ──
        self._cur_spots = []   # [(x, y, mu, label), ...] for hover
        self._annot = None

        self._build()

        # ── Keyboard shortcuts ──
        self.bind("<Control-o>",        lambda e: self.open_dialog())
        self.bind("<Control-O>",        lambda e: self.load_overlay())
        self.bind("<Control-Shift-O>",  lambda e: self.load_overlay())
        self.bind("<Left>",             self._key_left)
        self.bind("<Right>",            self._key_right)
        self.bind("<Up>",               self._key_up)
        self.bind("<Down>",             self._key_down)

        if path:
            self.load(path)

    # ── UI 빌드 ─────────────────────────────────────────────────────────────────
    def _update_file_labels(self):
        """툴바 파일 아이콘 레이블 갱신 (툴팁 내용은 hover 시 live 로딩됨)."""
        if self._tree_source == "overlay" and self.path2:
            name = os.path.basename(self.path2)
        elif self.path:
            name = os.path.basename(self.path)
        else:
            name = "—"
        if len(name) > 28:
            name = name[:25] + "…"
        self._file_info_lbl.config(text=f"📄 {name}")

    def _file_tooltip_text(self):
        """hover tooltip: primary + overlay 파일명 (▶ = 현재 트리)."""
        lines = []
        if self.path:
            m = "▶" if self._tree_source == "primary" else " "
            lines.append(f"{m}  Primary : {os.path.basename(self.path)}  [{self.modality}]")
        if self.path2:
            m = "▶" if self._tree_source == "overlay" else " "
            lines.append(f"{m}  Overlay : {os.path.basename(self.path2)}  [{self.modality2}]")
        return "\n".join(lines) if lines else ""

    def _build(self):
        # ── 툴바 1행: 파일 조작 + 패널/트리 토글 ──
        bar1 = ttk.Frame(self, padding=(6, 4, 6, 2))
        bar1.pack(fill="x")
        ttk.Button(bar1, text="Open…",          command=self.open_dialog).pack(side="left")
        ttk.Button(bar1, text="Overlay…",        command=self.load_overlay).pack(side="left", padx=(4, 0))
        ttk.Button(bar1, text="Spot Settings…",  command=self.open_spot_settings).pack(side="left", padx=(4, 0))
        self._expand_btn = ttk.Button(bar1, text="Expand ▾", command=self._toggle_expand)
        self._expand_btn.pack(side="left", padx=(8, 0))
        ttk.Checkbutton(bar1, text="Hide PHI", variable=self.hide_phi, command=self.reload).pack(side="left", padx=(8, 0))
        ttk.Separator(bar1, orient="vertical").pack(side="left", fill="y", padx=(8, 8))
        self._panel_btn = ttk.Button(bar1, text="Map / Summary ▶",
                                     command=self._toggle_right_panel)
        self._panel_btn.pack(side="left")
        self._tree_toggle_btn = ttk.Button(bar1, text="🌲 Tree: Primary",
                                           command=self._toggle_tree_source,
                                           state="disabled")
        self._tree_toggle_btn.pack(side="left", padx=(4, 0))
        # File info label — compact; hover tooltip shows full names
        self._file_info_lbl = ttk.Label(bar1, text="📄 —", foreground="#667085",
                                        cursor="hand2")
        self._file_info_lbl.pack(side="right", padx=(0, 4))
        _Tooltip(self._file_info_lbl, self._file_tooltip_text)

        # ── 툴바 2행: 검색 ──
        bar2 = ttk.Frame(self, padding=(6, 0, 6, 4))
        bar2.pack(fill="x")
        ttk.Label(bar2, text="Search:").pack(side="left")
        self.q = tk.StringVar()
        e = ttk.Entry(bar2, textvariable=self.q)
        e.pack(side="left", fill="x", expand=True, padx=(4, 0))
        e.bind("<Return>", lambda ev: self.search())
        ttk.Button(bar2, text="Next", command=self.search).pack(side="left", padx=(4, 0))
        self._search_status_lbl = ttk.Label(bar2, text="", foreground="#667085")
        self._search_status_lbl.pack(side="left", padx=(8, 0))
        ttk.Separator(bar2, orient="horizontal").pack(fill="x", pady=(4, 0))

        # ── 메인 영역 ──
        main = ttk.Frame(self)
        main.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        # ── 왼쪽: 태그 트리 ──
        left = ttk.Frame(main)
        cols = ("vr", "name", "value")
        self.tree = ttk.Treeview(left, columns=cols, show="tree headings")
        self.tree.heading("#0", text="Tag")
        self.tree.heading("vr", text="VR")
        self.tree.heading("name", text="Name")
        self.tree.heading("value", text="Value")
        self.tree.column("#0", width=210, stretch=False)
        self.tree.column("vr", width=40, stretch=False, anchor="center")
        self.tree.column("name", width=330, stretch=False)
        self.tree.column("value", width=300)
        ys = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.tag_configure("seq",     foreground="#1F4E79")
        self.tree.tag_configure("item",    foreground="#23292F", font=("TkDefaultFont", 10, "bold"))
        self.tree.tag_configure("private", foreground="#8A5A00")
        self.tree.tag_configure("phi",     foreground="#98A2B3")
        left.pack(side="left", fill="both", expand=True)

        # ── 오른쪽: Notebook (처음에는 숨김) ──
        right = ttk.Notebook(main)
        self.nb = right
        self._right_frame_parent = main  # pack 참조용

        # Tab 0: Spot map / Dose map
        self._map_frame = ttk.Frame(right)
        self._build_map_tab(self._map_frame)
        right.add(self._map_frame, text="Map")

        # Tab 1: Summary
        self.summ = tk.Text(right, wrap="none", font=("Courier", 11))
        right.add(self.summ, text="Summary")
        # right is NOT packed here — starts hidden

    def _toggle_right_panel(self):
        if self._right_visible:
            self.nb.pack_forget()
            self._panel_btn.config(text="Map / Summary ▶")
            self._right_visible = False
        else:
            self.nb.pack(side="right", fill="both", expand=True, padx=(4, 0))
            self._panel_btn.config(text="◀ Map / Summary")
            self._right_visible = True

    def _build_map_tab(self, parent):
        """Spot map + Dose map 공용 프레임을 구성한다."""

        # ── RTDOSE 전용 컨트롤 (2 rows) ──
        self._dose_ctrl = ttk.Frame(parent)

        # Row 1: direction + slice slider
        dose_row1 = ttk.Frame(self._dose_ctrl)
        dose_row1.pack(fill="x")
        ttk.Label(dose_row1, text="Direction:").pack(side="left")
        self.dose_dir = tk.StringVar(value="Axial")
        dir_cb = ttk.Combobox(dose_row1, textvariable=self.dose_dir,
                              values=["Axial", "Coronal", "Sagittal"],
                              width=10, state="readonly")
        dir_cb.pack(side="left", padx=(2, 12))
        dir_cb.bind("<<ComboboxSelected>>", lambda _: self._on_dose_dir_change())
        ttk.Label(dose_row1, text="Slice:").pack(side="left")
        self.dose_idx = tk.IntVar(value=0)
        self.dose_slider = ttk.Scale(dose_row1, variable=self.dose_idx,
                                     orient="horizontal", from_=0, to=1,
                                     command=lambda _: self._on_dose_slider())
        self.dose_slider.pack(side="left", fill="x", expand=True, padx=(4, 4))
        self.dose_slice_lbl = ttk.Label(dose_row1, text="0 / 0", width=8)
        self.dose_slice_lbl.pack(side="left")

        # Row 2: W/L (Window/Level) sliders
        dose_row2 = ttk.Frame(self._dose_ctrl)
        dose_row2.pack(fill="x", pady=(2, 0))
        ttk.Label(dose_row2, text="Min:").pack(side="left")
        self._wl_min_lbl = ttk.Label(dose_row2, text="0.000 Gy", width=9)
        self._wl_min_lbl.pack(side="left")
        self._wl_min_sl = ttk.Scale(dose_row2, variable=self.dose_vmin,
                                    orient="horizontal", from_=0.0, to=1.0,
                                    command=lambda _: self._on_wl_change())
        self._wl_min_sl.pack(side="left", fill="x", expand=True, padx=(2, 8))
        ttk.Label(dose_row2, text="Max:").pack(side="left")
        self._wl_max_lbl = ttk.Label(dose_row2, text="1.000 Gy", width=9)
        self._wl_max_lbl.pack(side="left")
        self._wl_max_sl = ttk.Scale(dose_row2, variable=self.dose_vmax,
                                    orient="horizontal", from_=0.0, to=1.0,
                                    command=lambda _: self._on_wl_change())
        self._wl_max_sl.pack(side="left", fill="x", expand=True, padx=(2, 4))
        ttk.Button(dose_row2, text="Reset", command=self._reset_wl).pack(side="left", padx=(4, 0))

        # 처음에는 숨김 (RTDOSE 로드 시 표시)
        self._dose_ctrl.pack_forget()

        # ── Overlay 컨트롤 (RTPLAN / RTRECORD spot map용) ──
        self._overlay_ctrl = ttk.Frame(parent)
        ttk.Label(self._overlay_ctrl, text="Show:").pack(side="left", padx=(4, 0))
        ttk.Checkbutton(self._overlay_ctrl, text="Primary",
                        variable=self._show_primary_spots,
                        command=self._redraw_cur_cp).pack(side="left", padx=(4, 0))
        ttk.Checkbutton(self._overlay_ctrl, text="Overlay",
                        variable=self._show_overlay_spots,
                        command=self._redraw_cur_cp).pack(side="left", padx=(4, 0))
        ttk.Separator(self._overlay_ctrl, orient="vertical").pack(
            side="left", fill="y", padx=(8, 8))
        ttk.Checkbutton(self._overlay_ctrl, text="Deviation arrows",
                        variable=self._show_deviations,
                        command=self._redraw_cur_cp).pack(side="left")
        ttk.Button(self._overlay_ctrl, text="Clear overlay",
                   command=self._clear_overlay).pack(side="left", padx=(8, 0))
        self._overlay_ctrl.pack_forget()

        # ── matplotlib 캔버스 ──
        self.fig = Figure(figsize=(5, 5), dpi=100)
        self.ax  = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent)
        NavigationToolbar2Tk(self.canvas, parent).update()
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.canvas.mpl_connect("motion_notify_event", self._on_map_hover)
        self.canvas.mpl_connect("scroll_event",        self._on_map_scroll)

    # ── 파일 로드 ───────────────────────────────────────────────────────────────
    def open_dialog(self):
        p = filedialog.askopenfilename(
            title="DICOM 파일 열기",
            filetypes=[("DICOM", "*.dcm"), ("All", "*")])
        if p:
            self.load(p)

    def reload(self):
        if self.path:
            self.load(self.path)

    def load(self, path):
        try:
            self.ds = pydicom.dcmread(path, force=True)
        except Exception as ex:
            messagebox.showerror("열기 실패", str(ex))
            return
        self.path = path
        self.modality = detect_modality(self.ds)
        self.title(f"DICOM RT Viewer — {self.modality}  {os.path.basename(path)}")
        self._update_file_labels()

        # 트리 초기화
        self._tree_source = "primary"
        self._populate_tree(self.ds)

        # 탭 레이블 + 컨트롤 표시 여부
        self._dose_im   = None
        self._dose_cbar = None
        self._cur_cp_seq = None
        self._cur_cp_k   = 0
        self._cur_beam_num = None

        if self.modality == "RTDOSE":
            self.nb.tab(0, text="Dose map")
            self._dose_ctrl.pack(fill="x", padx=4, pady=2,
                                 before=self.canvas.get_tk_widget())
            self._overlay_ctrl.pack_forget()
            self._init_dose_view()
        else:
            self.nb.tab(0, text="Spot map")
            self._dose_ctrl.pack_forget()
            self.ax.clear()
            self.canvas.draw()
            # Keep overlay ctrl visible only if overlay is loaded
            if self.ds2 is not None:
                self._overlay_ctrl.pack(fill="x", padx=4, pady=2,
                                        before=self.canvas.get_tk_widget())

        self.summary()
        self._search_hits, self._search_pos, self._last_q = [], 0, None

    # ── 트리 채우기 ─────────────────────────────────────────────────────────────
    def _populate_tree(self, ds):
        """주어진 Dataset으로 태그 트리를 다시 채운다."""
        self.tree.delete(*self.tree.get_children())
        self.nodes.clear()
        self._fill("", ds, ctx=None)
        for iid in self.tree.get_children():
            if "seq" in self.tree.item(iid, "tags"):
                self.tree.item(iid, open=True)

    def _toggle_tree_source(self):
        """Primary / Overlay 트리 소스를 전환한다."""
        if self.ds2 is None:
            return
        if self._tree_source == "primary":
            self._tree_source = "overlay"
            self._populate_tree(self.ds2)
            self._tree_toggle_btn.config(text="🌲 Tree: Overlay")
        else:
            self._tree_source = "primary"
            self._populate_tree(self.ds)
            self._tree_toggle_btn.config(text="🌲 Tree: Primary")
        self._update_file_labels()
        self._cur_cp_seq = None  # reset CP context on tree switch
        self._cur_cp_k = 0
        self._cur_beam_num = None

    def _item_label(self, seq_kw, k, item):
        # RT Record / RT Plan 공용 빔 시퀀스
        if seq_kw in ("TreatmentSessionIonBeamSequence", "IonBeamSequence"):
            name = item.get("BeamName", "")
            num  = item.get("ReferencedBeamNumber", item.get("BeamNumber", ""))
            fx   = item.get("CurrentFractionNumber", None)
            term = item.get("TreatmentTerminationStatus", "")
            extra = f" · fx {fx}" if fx is not None else ""
            extra += f" · {term}" if term else ""
            return f"Beam {num}  {name}{extra}"

        # Control point 시퀀스
        if seq_kw in ("IonControlPointDeliverySequence", "IonControlPointSequence"):
            e = item.get("NominalBeamEnergy", None)
            n = item.get("NumberOfScanSpotPositions", "")
            if k % 2 == 1:
                return f"CP {k}  ·  layer {k // 2 + 1} end"
            es = f"{float(e):.2f} MeV/u" if e is not None else "E inherited"
            return f"CP {k}  ·  layer {k // 2 + 1}  ·  {es}  ·  {n} spots"

        # RTPLAN 전용
        if seq_kw == "FractionGroupSequence":
            fg  = item.get("FractionGroupNumber", k)
            num = item.get("NumberOfFractionsPlanned", "?")
            return f"Fraction Group {fg}  ·  {num} fractions planned"

        if seq_kw == "ReferencedBeamSequence":
            bnum = item.get("ReferencedBeamNumber", k)
            dose = item.get("BeamDose", "")
            mu   = item.get("BeamMeterset", "")
            s = f"Beam ref {bnum}"
            if dose: s += f"  ·  dose {float(dose):.4f} Gy"
            if mu:   s += f"  ·  MU {float(mu):.2f}"
            return s

        if seq_kw == "DoseReferenceSequence":
            return f"Dose ref {item.get('DoseReferenceNumber', k)}  {item.get('DoseReferenceDescription', '')}"

        if seq_kw == "PatientSetupSequence":
            return f"Setup {k}  ·  {item.get('SetupTechnique', '')}  {item.get('PatientPosition', '')}"

        if seq_kw == "ToleranceTableSequence":
            return f"Tolerance {item.get('ToleranceTableNumber', k)}  {item.get('ToleranceTableLabel', '')}"

        # RTDOSE 전용
        if seq_kw == "ReferencedRTPlanSequence":
            return f"Ref Plan  {item.get('ReferencedSOPInstanceUID', k)}"

        return f"Item {k}"

    def _fill(self, parent, ds, ctx):
        for el in ds:
            key    = (el.tag.group, el.tag.element)
            tagtxt = f"({el.tag.group:04X},{el.tag.element:04X})"
            name   = el.name if el.name else "Private tag"
            tags   = []
            if el.tag.is_private:
                tags.append("private")
                if el.tag.group == 0x3009 and el.tag.element in IMPAC_GUESS:
                    name = f"IMPAC · {IMPAC_GUESS[el.tag.element]}"
            if el.VR == "SQ":
                iid = self.tree.insert(parent, "end", text=tagtxt,
                                       values=(el.VR, name, f"[{len(el.value)} items]"),
                                       tags=("seq",))
                self.nodes[iid] = (el, ctx)
                for k, item in enumerate(el.value):
                    lab = self._item_label(el.keyword, k, item)
                    # Propagate beam_num into CP contexts
                    if el.keyword in ("TreatmentSessionIonBeamSequence", "IonBeamSequence"):
                        beam_num = item.get("ReferencedBeamNumber",
                                            item.get("BeamNumber", k))
                    else:
                        beam_num = ctx.get("beam_num") if ctx else None
                    sub_ctx = dict(seq=el.keyword, index=k, parent_seq=el.value,
                                   beam_num=beam_num)
                    sub = self.tree.insert(iid, "end", text=f"  > {k}",
                                          values=("", lab, ""), tags=("item",))
                    self.nodes[sub] = (item, sub_ctx)
                    self._fill(sub, item, ctx=sub_ctx)
                continue
            if key in PHI and self.hide_phi.get():
                val  = "(hidden)"
                tags.append("phi")
            else:
                val = self._short(el)
            iid = self.tree.insert(parent, "end", text=tagtxt,
                                   values=(el.VR, name, val), tags=tuple(tags))
            self.nodes[iid] = (el, ctx)

    def _short(self, el):
        v = el.value
        if el.VR in ("UN", "OB", "OW") or isinstance(v, (bytes, bytearray)):
            return as_text(v)
        if (isinstance(v, (list, tuple, pydicom.multival.MultiValue)) or
                (hasattr(v, "__len__") and not isinstance(v, str) and len(v) > 1)):
            vals = list(v)
            head = ", ".join(f"{x:.3f}" if isinstance(x, float) else str(x) for x in vals[:6])
            return f"[{len(vals)}] {head}{' …' if len(vals) > 6 else ''}"
        s = str(v)
        return s if len(s) < 90 else s[:90] + " …"

    # ── 태그 선택 ───────────────────────────────────────────────────────────────
    def on_select(self, _ev=None):
        sel = self.tree.selection()
        if not sel:
            return
        obj, ctx = self.nodes.get(sel[0], (None, None))

        if isinstance(obj, pydicom.Dataset):
            if ctx and ctx.get("seq") in ("IonControlPointDeliverySequence",
                                          "IonControlPointSequence"):
                beam_num = ctx.get("beam_num")
                self.draw_cp(ctx["parent_seq"], ctx["index"], beam_num=beam_num)
            return

        el = obj
        if el is None:
            return
        if el.keyword in ("ScanSpotPositionMap", "ScanSpotMetersetsDelivered",
                          "ScanSpotMetersetWeights") and ctx:
            beam_num = ctx.get("beam_num")
            self.draw_cp(ctx["parent_seq"], ctx["index"], beam_num=beam_num)

    # ── Spot map (RTRECORD / RTPLAN) ─────────────────────────────────────────
    def draw_cp(self, seq, k, beam_num=None):
        k0 = k - (k % 2)
        cp = seq[k0]
        e, j = None, k0
        while j >= 0 and e is None:
            e = seq[j].get("NominalBeamEnergy", None)
            j -= 1

        # Store for keyboard navigation and redraw
        self._cur_cp_seq = seq
        self._cur_cp_k   = k0
        self._cur_beam_num = beam_num

        self.ax.clear()
        if self._dose_cbar:
            self._dose_cbar.remove()
            self._dose_cbar = None

        if "ScanSpotPositionMap" not in cp:
            self.canvas.draw()
            return

        p  = a1(cp.ScanSpotPositionMap).reshape(-1, 2)
        mk = ("ScanSpotMetersetsDelivered" if "ScanSpotMetersetsDelivered" in cp
              else "ScanSpotMetersetWeights")
        m  = a1(cp.get(mk, [])) if mk in cp else np.ones(len(p))
        fix = (p < FIXED_LIMIT).any(axis=1)
        q, mq = p[~fix], m[~fix]

        es = f"{float(e):.2f} MeV/u" if e is not None else "E ?"
        layer = k0 // 2 + 1

        # ── Overlay present? ──
        _, cp2 = self._get_overlay_cp(beam_num, k0) if beam_num is not None else (None, None)

        if cp2 is not None and "ScanSpotPositionMap" in cp2:
            p2  = a1(cp2.ScanSpotPositionMap).reshape(-1, 2)
            mk2 = ("ScanSpotMetersetsDelivered" if "ScanSpotMetersetsDelivered" in cp2
                   else "ScanSpotMetersetWeights")
            m2  = a1(cp2.get(mk2, [])) if mk2 in cp2 else np.ones(len(p2))
            fix2 = (p2 < FIXED_LIMIT).any(axis=1)
            q2, mq2 = p2[~fix2], m2[~fix2]

            # Figure out which is plan vs record
            mod1 = self.modality
            mod2 = self.modality2
            if mod1 == "RTPLAN" and mod2 == "RTRECORD":
                pplan, prec = q, q2
                mplan, mrec = mq, mq2
            elif mod1 == "RTRECORD" and mod2 == "RTPLAN":
                pplan, prec = q2, q
                mplan, mrec = mq2, mq
            else:
                # Both same modality — treat ds as A, ds2 as B
                pplan, prec = q, q2
                mplan, mrec = mq, mq2

            # Path line (show if at least one side is visible)
            if self._show_primary_spots.get() or self._show_overlay_spots.get():
                ref_pts = pplan if self._show_primary_spots.get() else prec
                self.ax.plot(ref_pts[:, 0], ref_pts[:, 1], "-",
                             color=self._spot_cfg["path_color"], lw=0.6, zorder=1)

            # Plan: open circles
            if self._show_primary_spots.get() and len(pplan):
                s_plan = 20 + 80 * (mplan / mplan.max()) if mplan.max() > 0 else 30
                self.ax.scatter(pplan[:, 0], pplan[:, 1], s=s_plan,
                                facecolors="none",
                                edgecolors=self._spot_cfg["plan_color"], linewidths=1.2,
                                zorder=2, label=self._spot_cfg["plan_label"])

            # Record: filled circles
            if self._show_overlay_spots.get() and len(prec):
                s_rec = 8 + 40 * (mrec / mrec.max()) if mrec.max() > 0 else 15
                self.ax.scatter(prec[:, 0], prec[:, 1], s=s_rec,
                                color=self._spot_cfg["record_color"], zorder=3, alpha=0.75,
                                label=self._spot_cfg["record_label"])

            # Deviation arrows
            n_pair = min(len(pplan), len(prec))
            if self._show_deviations.get() and n_pair > 0:
                dp = prec[:n_pair] - pplan[:n_pair]
                dists = np.hypot(dp[:, 0], dp[:, 1])
                for i in range(n_pair):
                    self.ax.annotate("",
                        xy=(prec[i, 0], prec[i, 1]),
                        xytext=(pplan[i, 0], pplan[i, 1]),
                        arrowprops=dict(arrowstyle="->",
                                        color=self._spot_cfg["arrow_color"],
                                        lw=0.8, shrinkA=0, shrinkB=0),
                        zorder=4)
                rms = float(np.sqrt(np.mean(dists**2)))
                mx  = float(dists.max())
                title_extra = f"  ·  RMS Δ={rms:.2f} mm  max Δ={mx:.2f} mm"
            else:
                title_extra = ""

            if self._show_primary_spots.get() or self._show_overlay_spots.get():
                self.ax.legend(fontsize=8, frameon=False, loc="best")
            self._cur_spots = (
                ([(float(pplan[i, 0]), float(pplan[i, 1]), float(mplan[i]),
                   self._spot_cfg["plan_label"])
                  for i in range(len(pplan))] if self._show_primary_spots.get() else []) +
                ([(float(prec[i, 0]),  float(prec[i, 1]),  float(mrec[i]),
                   self._spot_cfg["record_label"])
                  for i in range(len(prec))] if self._show_overlay_spots.get() else [])
            )

        else:
            # No overlay — original single-file rendering
            self.ax.plot(q[:, 0], q[:, 1], "-",
                         color=self._spot_cfg["path_color"], lw=0.7, zorder=1)
            s = 8 + 60 * (mq / mq.max()) if len(mq) and mq.max() > 0 else 10
            self.ax.scatter(q[:, 0], q[:, 1], s=s,
                            color=self._spot_cfg["single_color"], zorder=2)
            if len(q):
                show_first = self._spot_cfg.get("show_first", True)
                show_last  = self._spot_cfg.get("show_last",  True)
                if show_first:
                    lbl = self._spot_cfg.get("first_label", "first") or None
                    self.ax.scatter(*q[0],  s=90,
                                    color=self._spot_cfg["first_color"],
                                    zorder=3, label=lbl)
                if show_last:
                    lbl = self._spot_cfg.get("last_label", "last") or None
                    self.ax.scatter(*q[-1], s=90, marker="s",
                                    color=self._spot_cfg["last_color"],
                                    zorder=3, label=lbl)
                if show_first or show_last:
                    self.ax.legend(fontsize=8, frameon=False, loc="best")
            self._cur_spots = [(float(q[i, 0]), float(q[i, 1]), float(mq[i]), "")
                               for i in range(len(q))]
            title_extra = ""

        title = (f"CP {k0}  layer {layer}  {es}  {len(p)} spots"
                 + (f"  fixed {int(fix.sum())}" if fix.any() else "")
                 + title_extra)
        self.ax.set_title(title, fontsize=9)
        self.ax.set_xlabel("x (mm)")
        self.ax.set_ylabel("y (mm)")
        self.ax.set_aspect("equal")
        self.ax.grid(alpha=0.25)
        self.fig.tight_layout()
        self._init_annot()
        self.canvas.draw()


    # ── Overlay helpers ──────────────────────────────────────────────────────
    def load_overlay(self):
        p = filedialog.askopenfilename(
            title="Overlay DICOM 파일 열기 (RT Plan / RT Record)",
            filetypes=[("DICOM", "*.dcm"), ("All", "*")])
        if not p:
            return
        try:
            ds2 = pydicom.dcmread(p, force=True)
        except Exception as ex:
            messagebox.showerror("열기 실패", str(ex))
            return
        mod2 = detect_modality(ds2)
        if mod2 not in ("RTPLAN", "RTRECORD"):
            messagebox.showwarning("형식 불일치",
                f"Overlay는 RTPLAN 또는 RTRECORD 파일이어야 합니다.\n(감지된 modality: {mod2})")
            return
        self.ds2 = ds2
        self.path2 = p
        self.modality2 = mod2
        self._update_file_labels()
        self._overlay_ctrl.pack(fill="x", padx=4, pady=2,
                                before=self.canvas.get_tk_widget())
        self._tree_toggle_btn.config(state="normal", text="🌲 Tree: Primary")
        self._redraw_cur_cp()

    def _clear_overlay(self):
        self.ds2 = None
        self.path2 = None
        self.modality2 = None
        self._overlay_ctrl.pack_forget()
        self._tree_toggle_btn.config(state="disabled", text="🌲 Tree: Primary")
        if self._tree_source == "overlay" and self.ds is not None:
            self._tree_source = "primary"
            self._populate_tree(self.ds)
        self._update_file_labels()
        self._redraw_cur_cp()

    def _redraw_cur_cp(self):
        if self._cur_cp_seq is not None:
            self.draw_cp(self._cur_cp_seq, self._cur_cp_k, self._cur_beam_num)

    def _get_overlay_cp(self, beam_num, k0):
        """비교 파일에서 beam_num에 해당하는 CP를 찾는다.
        tree_source=primary → ds2에서 탐색
        tree_source=overlay → ds에서 탐색 (overlay 트리 열람 시)
        """
        # Which dataset is the "other" side?
        other = self.ds if self._tree_source == "overlay" else self.ds2
        if other is None or beam_num is None:
            return None, None
        for attr in ("TreatmentSessionIonBeamSequence", "IonBeamSequence"):
            beams = other.get(attr, [])
            for b in beams:
                bn = b.get("ReferencedBeamNumber", b.get("BeamNumber", None))
                if bn is not None and int(bn) == int(beam_num):
                    for cattr in ("IonControlPointDeliverySequence",
                                  "IonControlPointSequence"):
                        seq2 = b.get(cattr, [])
                        if seq2 and k0 < len(seq2):
                            return seq2, seq2[k0]
        return None, None

    # ── Dose map (RTDOSE) ────────────────────────────────────────────────────
    def _dose_array(self):
        """(nz, ny, nx) float32 Gy 배열을 반환한다."""
        arr = self.ds.pixel_array.astype(np.float32)
        scale = float(getattr(self.ds, "DoseGridScaling", 1.0))
        return arr * scale

    def _dose_shape(self):
        """(nz, ny, nx) 반환."""
        arr = self.ds.pixel_array
        if arr.ndim == 2:
            return (1, arr.shape[0], arr.shape[1])
        return arr.shape  # (nz, ny, nx)

    def _init_dose_view(self):
        """RTDOSE 로드 시 슬라이더 범위를 설정하고 중간 슬라이스를 표시한다."""
        try:
            nz, ny, nx = self._dose_shape()
        except Exception:
            return
        self._dose_dims = (nz, ny, nx)
        self._update_dose_slider_range()
        mid = self._dose_nslices() // 2
        self.dose_idx.set(mid)
        # Init W/L from actual dose max
        try:
            arr = self._dose_array()
            self._init_wl(float(arr.max()))
        except Exception:
            self._init_wl(1.0)
        self._show_dose_slice()
        self.nb.select(0)

    def _dose_nslices(self):
        d = self.dose_dir.get()
        nz, ny, nx = self._dose_dims
        return {"Axial": nz, "Coronal": ny, "Sagittal": nx}[d]

    def _update_dose_slider_range(self):
        n = self._dose_nslices()
        self.dose_slider.configure(from_=0, to=max(0, n - 1))

    def _on_dose_dir_change(self):
        self._update_dose_slider_range()
        n = self._dose_nslices()
        self.dose_idx.set(n // 2)
        self._show_dose_slice()

    def _on_dose_slider(self):
        self._show_dose_slice()

    def _show_dose_slice(self):
        if self.ds is None or self.modality != "RTDOSE":
            return
        try:
            arr = self._dose_array()
            if arr.ndim == 2:
                arr = arr[np.newaxis]
            nz, ny, nx = arr.shape
            d   = self.dose_dir.get()
            idx = int(self.dose_idx.get())
            idx = max(0, min(idx, {"Axial": nz-1, "Coronal": ny-1, "Sagittal": nx-1}[d]))
            self.dose_idx.set(idx)

            if   d == "Axial":    sl = arr[idx, :, :]
            elif d == "Coronal":  sl = arr[:, idx, :]
            else:                 sl = arr[:, :, idx]

            n = {"Axial": nz, "Coronal": ny, "Sagittal": nx}[d]
            self.dose_slice_lbl.config(text=f"{idx} / {n-1}")

            self.ax.clear()
            self._cur_spots = []
            self._annot = None
            if self._dose_cbar:
                try:
                    self._dose_cbar.remove()
                except Exception:
                    pass
                self._dose_cbar = None

            vmin = float(self.dose_vmin.get())
            vmax = float(self.dose_vmax.get())
            if vmax <= vmin:
                vmax = vmin + 1e-6

            im = self.ax.imshow(sl, cmap="jet", origin="lower",
                                vmin=vmin, vmax=vmax,
                                aspect="auto", interpolation="nearest")
            self._dose_cbar = self.fig.colorbar(im, ax=self.ax, fraction=0.035, pad=0.02)
            self._dose_cbar.set_label("Dose (Gy)", fontsize=9)

            ax_labels = {
                "Axial":    ("X (col)", "Y (row)"),
                "Coronal":  ("X (col)", "Z (frame)"),
                "Sagittal": ("Y (col)", "Z (frame)"),
            }
            xlabel, ylabel = ax_labels[d]
            self.ax.set_xlabel(xlabel, fontsize=9)
            self.ax.set_ylabel(ylabel, fontsize=9)
            self.ax.set_title(f"{d}  slice {idx}  ·  max {float(arr.max()):.3f} Gy  ·  "
                              f"slice mean {float(sl.mean()):.3f} Gy", fontsize=9)

            self.fig.tight_layout()
            self.canvas.draw()
        except Exception as ex:
            self.ax.clear()
            self.ax.text(0.5, 0.5, f"Dose display error:\n{ex}",
                         ha="center", va="center", transform=self.ax.transAxes)
            self.canvas.draw()

    # ── W/L helpers ─────────────────────────────────────────────────────────
    def _init_wl(self, max_dose):
        self._dose_vmax_abs = max(max_dose, 1e-6)
        for sl in (self._wl_min_sl, self._wl_max_sl):
            sl.configure(from_=0.0, to=self._dose_vmax_abs)
        self.dose_vmin.set(0.0)
        self.dose_vmax.set(self._dose_vmax_abs)
        self._wl_min_lbl.config(text=f"0.000 Gy")
        self._wl_max_lbl.config(text=f"{self._dose_vmax_abs:.3f} Gy")

    def _on_wl_change(self):
        vmin = float(self.dose_vmin.get())
        vmax = float(self.dose_vmax.get())
        if vmin >= vmax:
            self.dose_vmin.set(max(0.0, vmax - self._dose_vmax_abs * 0.01))
            vmin = float(self.dose_vmin.get())
        self._wl_min_lbl.config(text=f"{vmin:.3f} Gy")
        self._wl_max_lbl.config(text=f"{vmax:.3f} Gy")
        self._show_dose_slice()

    def _reset_wl(self):
        self.dose_vmin.set(0.0)
        self.dose_vmax.set(self._dose_vmax_abs)
        self._wl_min_lbl.config(text="0.000 Gy")
        self._wl_max_lbl.config(text=f"{self._dose_vmax_abs:.3f} Gy")
        self._show_dose_slice()

    # ── 키보드 단축키 ────────────────────────────────────────────────────────
    def _is_text_focused(self):
        w = self.focus_get()
        return isinstance(w, (tk.Text, ttk.Entry, tk.Entry))

    def _key_left(self, ev=None):
        if self._is_text_focused():
            return
        if self.modality == "RTDOSE":
            self._dose_step(-1)
        else:
            self._layer_step(-1)

    def _key_right(self, ev=None):
        if self._is_text_focused():
            return
        if self.modality == "RTDOSE":
            self._dose_step(1)
        else:
            self._layer_step(1)

    def _key_up(self, ev=None):
        if self._is_text_focused():
            return
        if self.modality == "RTDOSE":
            self._dose_step(-1)
        else:
            self._layer_step(-1)

    def _key_down(self, ev=None):
        if self._is_text_focused():
            return
        if self.modality == "RTDOSE":
            self._dose_step(1)
        else:
            self._layer_step(1)

    def _dose_step(self, delta):
        if self.modality != "RTDOSE":
            return
        idx = int(self.dose_idx.get()) + delta
        n   = self._dose_nslices()
        idx = max(0, min(idx, n - 1))
        self.dose_idx.set(idx)
        self._show_dose_slice()

    def _layer_step(self, delta):
        """RTPLAN / RTRECORD: 이전/다음 에너지 레이어로 이동."""
        if self._cur_cp_seq is None:
            return
        seq = self._cur_cp_seq
        k   = self._cur_cp_k + delta * 2  # each layer is 2 CPs apart
        k   = max(0, min(k, len(seq) - 2))
        if k % 2 != 0:
            k -= 1
        self.draw_cp(seq, k, self._cur_beam_num)

    # ── Hover tooltip ────────────────────────────────────────────────────────
    def _init_annot(self):
        """draw_cp 호출 후 axes 위에 tooltip annotation을 생성한다."""
        self._annot = self.ax.annotate(
            "", xy=(0, 0), xytext=(14, 14), textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.4", fc="#FFFBE6", ec="#F79009",
                      alpha=0.92, lw=1.2),
            arrowprops=dict(arrowstyle="->", color="#F79009", lw=1.0),
            fontsize=9, zorder=10,
        )
        self._annot.set_visible(False)

    def _on_map_hover(self, event):
        if self.modality not in ("RTPLAN", "RTRECORD"):
            return
        if event.inaxes != self.ax or self._annot is None or not self._cur_spots:
            if self._annot and self._annot.get_visible():
                self._annot.set_visible(False)
                self.canvas.draw_idle()
            return

        xm, ym = event.xdata, event.ydata
        xs = np.array([s[0] for s in self._cur_spots])
        ys = np.array([s[1] for s in self._cur_spots])

        # Find nearest spot; use display-pixel distance for threshold
        dists_data = np.hypot(xs - xm, ys - ym)
        idx = int(np.argmin(dists_data))
        pt_spot  = self.ax.transData.transform((xs[idx], ys[idx]))
        pt_mouse = np.array([event.x, event.y])
        pix_dist = float(np.hypot(*(pt_spot - pt_mouse)))

        if pix_dist < 14:
            x, y, mu, label = self._cur_spots[idx]
            prefix = f"[{label}]\n" if label else ""
            self._annot.xy = (x, y)
            self._annot.set_text(f"{prefix}x = {x:.2f} mm\ny = {y:.2f} mm\nMU = {mu:.4f}")
            self._annot.set_visible(True)
        else:
            self._annot.set_visible(False)
        self.canvas.draw_idle()

    # ── Scroll: layer / slice navigation ─────────────────────────────────────
    def _on_map_scroll(self, event):
        delta = -1 if event.button == "up" else 1
        if self.modality == "RTDOSE":
            self._dose_step(delta)
        elif self.modality in ("RTPLAN", "RTRECORD"):
            self._layer_step(delta)

    # ── Spot map settings ────────────────────────────────────────────────────
    def open_spot_settings(self):
        SpotMapSettingsDialog(self, self._spot_cfg, self._on_spot_cfg_apply)

    def _on_spot_cfg_apply(self, new_cfg):
        self._spot_cfg.update(new_cfg)
        self._redraw_cur_cp()

    # ── Summary ─────────────────────────────────────────────────────────────
    def summary(self):
        t = self.summ
        t.delete("1.0", "end")
        w = lambda s="": t.insert("end", s + "\n")
        ds = self.ds
        w(f"File        {os.path.basename(self.path)}")
        w(f"Modality    {ds.get('Modality', '')}   SOP {getattr(ds.get('SOPClassUID',''), 'name', ds.get('SOPClassUID',''))}")
        if not (self.hide_phi.get()):
            w(f"Patient     {ds.get('PatientName', '')}   ID {ds.get('PatientID', '')}")
        else:
            w(f"Patient     (hidden)   ID (hidden)")
        w(f"Study date  {ds.get('StudyDate', '')}   Study desc  {ds.get('StudyDescription', '')}")
        w(f"Series      {ds.get('SeriesDescription', '')}")
        w()
        if self.modality == "RTRECORD":
            self._summary_rtrecord(w)
        elif self.modality == "RTPLAN":
            self._summary_rtplan(w)
        elif self.modality == "RTDOSE":
            self._summary_rtdose(w)
        else:
            w(f"(No modality-specific summary for {self.modality})")

    def _summary_rtrecord(self, w):
        ds = self.ds
        mach = (ds.TreatmentMachineSequence[0].get("TreatmentMachineName", "")
                if "TreatmentMachineSequence" in ds else "")
        w(f"Machine     {mach}   Station {ds.get('StationName', '')}   SW {ds.get('SoftwareVersions', '')}")
        w(f"Treat date  {ds.get('TreatmentDate', '')} {ds.get('TreatmentTime', '')}   Dosimeter {ds.get('PrimaryDosimeterUnit', '')}")
        if "ReferencedRTPlanSequence" in ds:
            w(f"Ref. plan   {ds.ReferencedRTPlanSequence[0].ReferencedSOPInstanceUID}")
        beams = ds.get("TreatmentSessionIonBeamSequence", []) or ds.get("IonBeamSequence", [])
        for b in beams:
            w()
            cps  = (b.get("IonControlPointDeliverySequence", []) or
                    b.get("IonControlPointSequence", []))
            mkey = ("ScanSpotMetersetsDelivered" if "IonControlPointDeliverySequence" in b
                    else "ScanSpotMetersetWeights")
            w(f"Beam {b.get('ReferencedBeamNumber', b.get('BeamNumber', ''))}  {b.get('BeamName', '')}")
            if "CurrentFractionNumber" in b:
                fx   = int(b.CurrentFractionNumber)
                w(f"  fx {fx} ({'QA' if fx == 0 else 'treatment'})   "
                  f"Delivery {b.get('TreatmentDeliveryType', '')}   "
                  f"Termination {b.get('TreatmentTerminationStatus', '')}")
            if "SpecifiedPrimaryMeterset" in b:
                sp, dv = float(b.SpecifiedPrimaryMeterset), float(b.DeliveredPrimaryMeterset)
                w(f"  Primary meterset  specified {sp:.6e}  delivered {dv:.6e}  ratio {dv/sp:.5f}")
            nl = len(cps) // 2
            spots = fixed = 0
            inh = []
            for i in range(0, len(cps), 2):
                c = cps[i]
                if "NominalBeamEnergy" not in c:
                    inh.append(i)
                if "ScanSpotPositionMap" in c:
                    p = a1(c.ScanSpotPositionMap).reshape(-1, 2)
                    spots += len(p)
                    fixed += int((p < FIXED_LIMIT).any(axis=1).sum())
            w(f"  Control points {len(cps)} → layers {nl}   spots {spots}   fixed-coord spots {fixed}")
            if inh:
                w(f"  Energy inherited at CPs {inh}")
            imp = [(el.tag.element, as_text(el.value))
                   for el in b if el.tag.group == 0x3009 and el.tag.element >= 0x1000]
            if imp:
                w("  IMPAC private tags")
                for e_, v in imp:
                    w(f"    (3009,{e_:04X})  {v:<22} {IMPAC_GUESS.get(e_, '')}")

    def _summary_rtplan(self, w):
        ds = self.ds
        w(f"Plan label  {ds.get('RTPlanLabel', '')}   Name {ds.get('RTPlanName', '')}")
        w(f"Plan date   {ds.get('RTPlanDate', '')} {ds.get('RTPlanTime', '')}")
        w(f"Geometry    {ds.get('RTPlanGeometry', '')}")
        if "ReferencedStructureSetSequence" in ds:
            w(f"Struct set  {ds.ReferencedStructureSetSequence[0].ReferencedSOPInstanceUID}")

        # Fraction groups
        for fg in ds.get("FractionGroupSequence", []):
            w()
            w(f"Fraction Group {fg.get('FractionGroupNumber', '')}  ·  "
              f"{fg.get('NumberOfFractionsPlanned', '?')} fractions planned  ·  "
              f"{fg.get('NumberOfBeams', '?')} beams")
            for rb in fg.get("ReferencedBeamSequence", []):
                bnum = rb.get("ReferencedBeamNumber", "")
                dose = rb.get("BeamDose", None)
                mu   = rb.get("BeamMeterset", None)
                s = f"  Beam {bnum}"
                if dose: s += f"  ·  BeamDose {float(dose):.4f} Gy"
                if mu:   s += f"  ·  Meterset {float(mu):.2f}"
                w(s)

        # Beams
        beams = ds.get("IonBeamSequence", [])
        w()
        w(f"{'─'*60}")
        w(f"Beams ({len(beams)} total)")
        for b in beams:
            w()
            bname = b.get("BeamName", "")
            bnum  = b.get("BeamNumber", "")
            btype = b.get("BeamType", "")
            rad   = b.get("RadiationType", "")
            w(f"Beam {bnum}  {bname}  [{rad} {btype}]")
            mach = b.get("TreatmentMachineName", "")
            if mach:
                w(f"  Machine  {mach}")
            cps = b.get("IonControlPointSequence", [])
            nl  = len(cps) // 2
            cp0 = cps[0] if cps else None
            if cp0 and "IsocenterPosition" in cp0:
                iso = [f"{float(x):.2f}" for x in cp0.IsocenterPosition]
                w(f"  Isocenter  [{', '.join(iso)}] mm")
            if cp0 and "VirtualSourceAxisDistances" in cp0:
                vsad = [f"{float(x):.1f}" for x in cp0.VirtualSourceAxisDistances]
                w(f"  VSAD  [{', '.join(vsad)}] mm")
            energies = []
            spots_total = 0
            for i in range(0, len(cps), 2):
                c = cps[i]
                e = c.get("NominalBeamEnergy", None)
                if e is not None:
                    energies.append(float(e))
                if "NumberOfScanSpotPositions" in c:
                    spots_total += int(c.NumberOfScanSpotPositions)
            if energies:
                w(f"  Energy  {min(energies):.2f} – {max(energies):.2f} MeV/u  ·  {len(energies)} layers")
            w(f"  Control points {len(cps)} → layers {nl}   spots {spots_total}")

    def _summary_rtdose(self, w):
        ds = self.ds
        w(f"Dose type   {ds.get('DoseSummationType', '')}   Units {ds.get('DoseUnits', '')}")
        scale = float(getattr(ds, "DoseGridScaling", 1.0))
        w(f"GridScaling {scale:.6e} Gy/pixel")
        if "ReferencedRTPlanSequence" in ds:
            w(f"Ref. plan   {ds.ReferencedRTPlanSequence[0].ReferencedSOPInstanceUID}")
        try:
            shape = ds.pixel_array.shape
            w()
            w(f"Grid shape  {shape}  (frames × rows × cols) = (nz × ny × nx)")
            ps  = ds.get("PixelSpacing", [])
            gfs = ds.get("GridFrameOffsetVector", [])
            ipp = ds.get("ImagePositionPatient", [])
            if ps:
                dy, dx = float(ps[0]), float(ps[1])
                w(f"PixelSpacing  dy={dy} mm  dx={dx} mm")
            if gfs and len(gfs) > 1:
                dz = abs(float(gfs[1]) - float(gfs[0]))
                w(f"Frame step  dz={dz:.3f} mm  ({len(list(gfs))} frames)")
            if ipp:
                w(f"IPP (origin) [{', '.join(f'{float(x):.2f}' for x in ipp)}] mm")
            arr = ds.pixel_array.astype(np.float32) * scale
            w()
            w(f"Dose stats  max {arr.max():.4f} Gy  mean {arr.mean():.4f} Gy  "
              f"nonzero {int((arr > 0).sum())}/{arr.size} voxels")
        except Exception as ex:
            w(f"(pixel_array error: {ex})")

    # ── 유틸 ───────────────────────────────────────────────────────────────────
    def _toggle_expand(self):
        self._tree_expanded = not self._tree_expanded
        self.expand(self._tree_expanded)
        self._expand_btn.config(
            text="Collapse ▴" if self._tree_expanded else "Expand ▾")

    def expand(self, open_):
        def rec(iid):
            for c in self.tree.get_children(iid):
                self.tree.item(c, open=open_)
                rec(c)
        rec("")

    def search(self):
        q = self.q.get().strip().lower()
        if not q:
            return
        if self._last_q != q:
            self._last_q = q
            self._search_hits = []
            def rec(iid):
                for c in self.tree.get_children(iid):
                    it  = self.tree.item(c)
                    hay = (it["text"] + " " + " ".join(map(str, it["values"]))).lower()
                    if q in hay:
                        self._search_hits.append(c)
                    rec(c)
            rec("")
            self._search_pos = 0
        if not self._search_hits:
            self._search_status_lbl.config(text=f"'{q}' not found")
            return
        iid = self._search_hits[self._search_pos % len(self._search_hits)]
        self._search_pos += 1
        p = self.tree.parent(iid)
        while p:
            self.tree.item(p, open=True)
            p = self.tree.parent(p)
        self.tree.selection_set(iid)
        self.tree.see(iid)
        fname = os.path.basename(self.path) if self.path else ""
        self._search_status_lbl.config(
            text=f"hit {self._search_pos}/{len(self._search_hits)}")


# ── Spot Map Settings Dialog ───────────────────────────────────────────────
class SpotMapSettingsDialog(tk.Toplevel):
    """Spot map 표시 설정 다이얼로그 (색상, 레이블)."""

    DEFAULTS = {
        "single_color":  "#23292F",
        "show_first":    True,
        "first_label":   "first",
        "first_color":   "#2E90FA",
        "show_last":     True,
        "last_label":    "last",
        "last_color":    "#F79009",
        "path_color":    "#98A2B3",
        "plan_label":    "Plan",
        "plan_color":    "#1570EF",
        "record_label":  "Record",
        "record_color":  "#D92D20",
        "arrow_color":   "#F79009",
    }

    def __init__(self, parent, current_cfg, on_apply):
        super().__init__(parent)
        self.title("Spot Map Settings")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self._on_apply = on_apply

        # Working copy
        self._cfg = dict(current_cfg)

        pad = dict(padx=8, pady=4)
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)

        # ── Single file section ────────────────────────────────────────────
        ttk.Label(f, text="Single file", font=("TkDefaultFont", 10, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))

        self._add_color_row(f, 1, "Spot color",      "single_color")
        self._add_toggle_label_color_row(f, 2, "First spot",
                                         "show_first", "first_label", "first_color")
        self._add_toggle_label_color_row(f, 4, "Last spot",
                                         "show_last",  "last_label",  "last_color")
        self._add_color_row(f, 6, "Path line color", "path_color")

        ttk.Separator(f, orient="horizontal").grid(
            row=7, column=0, columnspan=3, sticky="ew", pady=10)

        # ── Overlay section ────────────────────────────────────────────────
        ttk.Label(f, text="Overlay (Plan vs Record)", font=("TkDefaultFont", 10, "bold")).grid(
            row=8, column=0, columnspan=3, sticky="w", pady=(0, 4))

        self._add_label_color_row(f, 9,  "Plan",   "plan_label",   "plan_color")
        self._add_label_color_row(f, 11, "Record", "record_label", "record_color")
        self._add_color_row(f, 13, "Deviation arrow color", "arrow_color")

        ttk.Separator(f, orient="horizontal").grid(
            row=14, column=0, columnspan=3, sticky="ew", pady=10)

        # ── Buttons ────────────────────────────────────────────────────────
        btn_frame = ttk.Frame(f)
        btn_frame.grid(row=15, column=0, columnspan=3, sticky="e")
        ttk.Button(btn_frame, text="Reset defaults",
                   command=self._reset).pack(side="left", padx=(0, 20))
        ttk.Button(btn_frame, text="Cancel",
                   command=self.destroy).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="OK",
                   command=self._ok).pack(side="left")

        self.update_idletasks()
        # Center over parent
        px, py = parent.winfo_x(), parent.winfo_y()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        self.geometry(f"+{px + pw//2 - w//2}+{py + ph//2 - h//2}")
        self.lift()
        self.focus_force()

    # ── Row builders ──────────────────────────────────────────────────────
    def _add_color_row(self, parent, row, label_text, key):
        ttk.Label(parent, text=label_text).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=3)
        swatch = tk.Button(parent, width=4, relief="solid",
                           bg=self._cfg[key],
                           command=lambda k=key: self._pick_color(k))
        swatch.grid(row=row, column=1, sticky="w", pady=3)
        setattr(self, f"_swatch_{key}", swatch)
        hex_lbl = ttk.Label(parent, text=self._cfg[key], foreground="#667085", width=9)
        hex_lbl.grid(row=row, column=2, sticky="w", padx=(6, 0))
        setattr(self, f"_hex_{key}", hex_lbl)

    def _add_toggle_label_color_row(self, parent, row, display,
                                     show_key, label_key, color_key):
        """Checkbox (show/hide) + label text entry on row; color picker on row+1."""
        var_show = tk.BooleanVar(value=bool(self._cfg.get(show_key, True)))
        ttk.Checkbutton(parent, text=f"{display} label:",
                        variable=var_show).grid(
            row=row, column=0, sticky="w", padx=(0, 4), pady=3)
        setattr(self, f"_var_{show_key}", var_show)
        var_lbl = tk.StringVar(value=str(self._cfg.get(label_key, display.lower())))
        ttk.Entry(parent, textvariable=var_lbl, width=14).grid(
            row=row, column=1, columnspan=2, sticky="w", pady=3)
        setattr(self, f"_var_{label_key}", var_lbl)
        self._add_color_row(parent, row + 1, f"{display} color", color_key)

    def _add_label_color_row(self, parent, row, display, label_key, color_key):
        """label text entry on row, color picker on row+1."""
        ttk.Label(parent, text=f"{display} label").grid(
            row=row, column=0, sticky="w", padx=(0, 12), pady=3)
        var = tk.StringVar(value=self._cfg[label_key])
        entry = ttk.Entry(parent, textvariable=var, width=14)
        entry.grid(row=row, column=1, columnspan=2, sticky="w", pady=3)
        setattr(self, f"_var_{label_key}", var)
        self._add_color_row(parent, row + 1, f"{display} color", color_key)

    # ── Actions ───────────────────────────────────────────────────────────
    def _pick_color(self, key):
        from tkinter.colorchooser import askcolor
        result = askcolor(color=self._cfg[key], parent=self,
                          title=f"Choose color — {key}")
        if result and result[1]:
            hex_color = result[1]
            self._cfg[key] = hex_color
            getattr(self, f"_swatch_{key}").config(bg=hex_color)
            getattr(self, f"_hex_{key}").config(text=hex_color)

    def _reset(self):
        for key, val in self.DEFAULTS.items():
            self._cfg[key] = val
            if hasattr(self, f"_swatch_{key}"):
                getattr(self, f"_swatch_{key}").config(bg=val)
                getattr(self, f"_hex_{key}").config(text=val)
            if hasattr(self, f"_var_{key}"):
                v = getattr(self, f"_var_{key}")
                if isinstance(v, tk.BooleanVar):
                    v.set(bool(val))
                else:
                    v.set(str(val))

    def _ok(self):
        for key in ("show_first", "show_last"):
            var = getattr(self, f"_var_{key}", None)
            if var is not None:
                self._cfg[key] = bool(var.get())
        for key in ("first_label", "last_label", "plan_label", "record_label"):
            var = getattr(self, f"_var_{key}", None)
            if var is not None:
                self._cfg[key] = var.get().strip()
        self._on_apply(self._cfg)
        self.destroy()


if __name__ == "__main__":
    App(sys.argv[1] if len(sys.argv) > 1 else None).mainloop()
