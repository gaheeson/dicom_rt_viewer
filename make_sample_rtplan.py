#!/usr/bin/env python3
"""
make_sample_rtplan.py — Generate a synthetic RT Ion Plan DICOM file
with prostate-shaped spot patterns for testing dicom_rt_viewer.py.

  python make_sample_rtplan.py           -> sample_rtplan.dcm
  python make_sample_rtplan.py out.dcm   -> custom filename

Spot layout: each energy layer fills a prostate-shaped cross-section
(ellipse ~38 x 30 mm, 4 mm spot spacing), scaled down slightly for
deeper layers to approximate a cone-shaped target volume.
"""
import sys
import datetime
import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import generate_uid, ExplicitVRLittleEndian

OUT = sys.argv[1] if len(sys.argv) > 1 else "sample_rtplan.dcm"

rng = np.random.default_rng(42)


# ── Prostate-shaped spot generator ──────────────────────────────────────────
def prostate_spots(scale=1.0, spacing_mm=4.0, cx=0.0, cy=0.0):
    """
    Return (x, y) spot positions filling a prostate cross-section.
    Shape: ellipse ~38 x 30 mm at scale=1.0, with a slight superior notch
    (seminal vesicle indentation).
    scale: 1.0 = proximal (largest), smaller toward distal.
    """
    W = 19.0 * scale   # half-width  (L-R)
    H = 15.0 * scale   # half-height (A-P)

    xs = np.arange(-W - spacing_mm, W + spacing_mm * 2, spacing_mm)
    ys = np.arange(-H - spacing_mm, H + spacing_mm * 2, spacing_mm)

    spots = []
    for x in xs:
        for y in ys:
            nx, ny = x / W, y / H
            if nx**2 + ny**2 > 1.0:
                continue
            # Superior notch (seminal vesicle region)
            if ny > 0.55 and abs(nx) > 0.45 * (1 - (ny - 0.55) / 0.45):
                continue
            spots.append((cx + x, cy + y))
    return spots


# ── DICOM CP builders ────────────────────────────────────────────────────────
def make_cp(energy_mev, spots_xy, mu_list, isocenter=(0, 0, 0), cum=0.0):
    cp = Dataset()
    cp.NominalBeamEnergy = float(energy_mev)
    cp.NumberOfScanSpotPositions = len(spots_xy)
    flat = [coord for x, y in spots_xy for coord in (float(x), float(y))]
    cp.ScanSpotPositionMap = flat
    cp.ScanSpotMetersetWeights = [float(m) for m in mu_list]
    cp.ScanSpotTuneID = "1"
    cp.NumberOfPaintings = 1
    cp.IsocenterPosition = [float(v) for v in isocenter]
    cp.VirtualSourceAxisDistances = [2733.0, 2711.0]
    cp.GantryAngle = 0.0
    cp.GantryRotationDirection = "NONE"
    cp.BeamLimitingDeviceAngle = 0.0
    cp.PatientSupportAngle = 0.0
    cp.ScanningSpotSize = [8.0, 8.0]
    cp.CumulativeMetersetWeight = float(cum)
    return cp


def make_cp_end(cum_mu):
    cp = Dataset()
    cp.CumulativeMetersetWeight = float(cum_mu)
    cp.NumberOfScanSpotPositions = 1
    cp.ScanSpotPositionMap = [0.0, 0.0]
    cp.ScanSpotMetersetWeights = [0.0]
    cp.NumberOfPaintings = 1
    return cp


def make_beam(beam_number, beam_name, gantry_angle, energy_layers):
    """
    energy_layers: list of (energy_mev, scale) tuples.
    scale controls the prostate cross-section size (1.0=proximal, smaller distally).
    """
    b = Dataset()
    b.BeamNumber = beam_number
    b.BeamName = beam_name
    b.BeamDescription = f"Prostate carbon-ion beam gantry {gantry_angle}deg"
    b.BeamType = "DYNAMIC"
    b.RadiationType = "ION"
    b.TreatmentMachineName = "HIMAC-Gantry1"
    b.PrimaryDosimeterUnit = "MU"
    b.FinalCumulativeMetersetWeight = 0.0

    cps = []
    cum = 0.0
    for energy, scale in energy_layers:
        spots = prostate_spots(scale=scale, spacing_mm=4.0)
        # MU: Gaussian-like profile, higher at center
        mus = []
        for x, y in spots:
            r2 = (x / (19 * scale))**2 + (y / (15 * scale))**2
            mu = float(rng.uniform(0.8, 1.2) * (1.2 - 0.4 * r2))
            mus.append(mu)
        cp = make_cp(energy, spots, mus, cum=cum)
        cps.append(cp)
        cum += sum(mus)
        cps.append(make_cp_end(cum))

    b.FinalCumulativeMetersetWeight = cum
    b.IonControlPointSequence = Sequence(cps)
    b.NumberOfControlPoints = len(cps)
    return b


# ── Energy layers: proximal -> distal (scale shrinks distally) ───────────────
layers_lt = [
    (152.0, 1.00),
    (158.0, 0.95),
    (165.0, 0.88),
    (172.0, 0.80),
    (179.0, 0.70),
    (186.0, 0.58),
    (192.0, 0.44),
]

layers_rt = [
    (150.0, 1.00),
    (156.0, 0.95),
    (163.0, 0.88),
    (170.0, 0.80),
    (177.0, 0.70),
    (184.0, 0.58),
    (190.0, 0.44),
]

beams = [
    make_beam(1, "B1_G000_LT",  0,   layers_lt),
    make_beam(2, "B2_G180_RT", 180,  layers_rt),
]

# ── FractionGroupSequence ────────────────────────────────────────────────────
fg = Dataset()
fg.FractionGroupNumber = 1
fg.NumberOfFractionsPlanned = 12
fg.NumberOfBeams = len(beams)
ref_beams = []
for b in beams:
    rb = Dataset()
    rb.ReferencedBeamNumber = b.BeamNumber
    rb.BeamMeterset = float(b.FinalCumulativeMetersetWeight)
    rb.BeamDose = round(float(b.FinalCumulativeMetersetWeight) / 5000.0, 4)
    ref_beams.append(rb)
fg.ReferencedBeamSequence = Sequence(ref_beams)

# ── DoseReferenceSequence ────────────────────────────────────────────────────
dr = Dataset()
dr.DoseReferenceNumber = 1
dr.DoseReferenceUID = generate_uid()
dr.DoseReferenceStructureType = "SITE"
dr.DoseReferenceDescription = "Prostate PTV"
dr.DoseReferenceType = "TARGET"
dr.TargetPrescriptionDose = 4.2   # Gy(RBE)/fx typical CIRT prostate

# ── PatientSetup / ToleranceTable ────────────────────────────────────────────
ps = Dataset()
ps.PatientSetupNumber = 1
ps.PatientPosition = "HFS"
ps.SetupTechnique = "ISOCENTRIC"

tt = Dataset()
tt.ToleranceTableNumber = 1
tt.ToleranceTableLabel = "DEFAULT"

# ── Main dataset ──────────────────────────────────────────────────────────────
now = datetime.datetime.now()
sop_uid = generate_uid()

file_meta = FileMetaDataset()
file_meta.MediaStorageSOPClassUID    = "1.2.840.10008.5.1.4.1.1.481.8"
file_meta.MediaStorageSOPInstanceUID = sop_uid
file_meta.TransferSyntaxUID          = ExplicitVRLittleEndian

ds = FileDataset(OUT, {}, file_meta=file_meta, preamble=b"\x00" * 128)
ds.is_implicit_VR = False
ds.is_little_endian = True

ds.SpecificCharacterSet       = "ISO_IR 192"
ds.SOPClassUID                = "1.2.840.10008.5.1.4.1.1.481.8"
ds.SOPInstanceUID             = sop_uid
ds.StudyDate                  = now.strftime("%Y%m%d")
ds.StudyTime                  = now.strftime("%H%M%S.%f")
ds.RTPlanDate                 = now.strftime("%Y%m%d")
ds.RTPlanTime                 = now.strftime("%H%M%S.%f")
ds.Modality                   = "RTPLAN"
ds.Manufacturer               = "SyntheticGen"
ds.ManufacturerModelName      = "make_sample_rtplan"
ds.SoftwareVersions           = "1.0"
ds.StudyInstanceUID           = generate_uid()
ds.SeriesInstanceUID          = generate_uid()
ds.StudyID                    = "PROSTATE01"
ds.SeriesNumber               = "1"
ds.InstanceNumber             = "1"
ds.RTPlanLabel                = "PROSTATE_CIRT"
ds.RTPlanName                 = "Sample Prostate Carbon-Ion Plan"
ds.RTPlanDescription          = "Synthetic prostate CIRT plan -- no clinical use"
ds.RTPlanGeometry             = "PATIENT"
ds.PatientName                = "Sample^Prostate"
ds.PatientID                  = "PROSTATE001"
ds.PatientBirthDate           = "19550101"
ds.PatientSex                 = "M"

rss = Dataset()
rss.ReferencedSOPClassUID    = "1.2.840.10008.5.1.4.1.1.481.3"
rss.ReferencedSOPInstanceUID = generate_uid()
ds.ReferencedStructureSetSequence = Sequence([rss])

ds.FractionGroupSequence   = Sequence([fg])
ds.IonBeamSequence         = Sequence(beams)
ds.DoseReferenceSequence   = Sequence([dr])
ds.PatientSetupSequence    = Sequence([ps])
ds.ToleranceTableSequence  = Sequence([tt])

pydicom.dcmwrite(OUT, ds)

print(f"Saved: {OUT}")
print(f"  Beams : {len(beams)}")
for b in beams:
    nl = len(b.IonControlPointSequence) // 2
    spot_counts = [int(b.IonControlPointSequence[i].NumberOfScanSpotPositions)
                   for i in range(0, len(b.IonControlPointSequence), 2)]
    print(f"  {b.BeamName:20s}  {nl} layers  spots/layer: {spot_counts}  MU={b.FinalCumulativeMetersetWeight:.1f}")
