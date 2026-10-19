#!/usr/bin/env python3
"""
make_sample_rtrecord.py — Generate a synthetic RT Ion Beams Treatment Record
that matches sample_rtplan.dcm but with realistic delivery deviations.

  python make_sample_rtrecord.py              -> sample_rtrecord.dcm
  python make_sample_rtrecord.py out.dcm      -> custom filename

  --plan PATH    RT Plan file to reference (default: sample_rtplan.dcm)
  --sigma MM     Gaussian spot position deviation σ in mm (default: 0.3)
  --mu-noise     MU delivery noise fraction (default: 0.005 = 0.5%)
  --fraction N   CurrentFractionNumber (0=QA, 1=Fx1, …; default: 1)

The record uses the same prostate spot positions as the plan but adds
small Gaussian position offsets so deviation arrows are visible in the viewer.
"""
import sys
import argparse
import datetime
import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import generate_uid, ExplicitVRLittleEndian

# ── Args ──────────────────────────────────────────────────────────────────────
ap = argparse.ArgumentParser()
ap.add_argument("out",          nargs="?",  default="sample_rtrecord.dcm")
ap.add_argument("--plan",       default="sample_rtplan.dcm")
ap.add_argument("--sigma",      type=float, default=0.3)
ap.add_argument("--mu-noise",   type=float, default=0.005)
ap.add_argument("--fraction",   type=int,   default=1)
args = ap.parse_args()

rng = np.random.default_rng(7)

# ── Load plan ─────────────────────────────────────────────────────────────────
plan_ds = pydicom.dcmread(args.plan, force=True)

plan_sop_uid = str(plan_ds.SOPInstanceUID)
plan_beams   = list(plan_ds.IonBeamSequence)

# ── RT Record CP builder ──────────────────────────────────────────────────────
def make_delivery_cp(plan_cp, sigma, mu_noise, delivered_mu_before):
    """Convert a plan CP to a delivery CP with position/MU noise."""
    cp = Dataset()

    # Spot positions: add small Gaussian offset per spot
    flat_plan = list(plan_cp.ScanSpotPositionMap)
    n_spots = len(flat_plan) // 2
    offsets = rng.normal(0.0, sigma, size=(n_spots, 2))
    flat_del = []
    for i in range(n_spots):
        flat_del.append(float(flat_plan[2 * i]     + offsets[i, 0]))
        flat_del.append(float(flat_plan[2 * i + 1] + offsets[i, 1]))
    cp.ScanSpotPositionMap = flat_del
    cp.NumberOfScanSpotPositions = n_spots

    # MU: add small noise
    plan_mus = list(plan_cp.ScanSpotMetersetWeights)
    if not isinstance(plan_mus, list):
        plan_mus = [float(plan_mus)]
    del_mus = [float(m) * (1.0 + rng.normal(0.0, mu_noise)) for m in plan_mus]
    cp.ScanSpotMetersetWeights = del_mus

    cp.NominalBeamEnergy           = float(plan_cp.NominalBeamEnergy)
    cp.ScanSpotTuneID              = getattr(plan_cp, "ScanSpotTuneID", "1")
    cp.NumberOfPaintings           = getattr(plan_cp, "NumberOfPaintings", 1)
    cp.IsocenterPosition           = list(plan_cp.IsocenterPosition)
    cp.VirtualSourceAxisDistances  = list(plan_cp.VirtualSourceAxisDistances)
    cp.GantryAngle                 = float(getattr(plan_cp, "GantryAngle", 0.0))
    cp.GantryRotationDirection     = getattr(plan_cp, "GantryRotationDirection", "NONE")
    cp.BeamLimitingDeviceAngle     = float(getattr(plan_cp, "BeamLimitingDeviceAngle", 0.0))
    cp.PatientSupportAngle         = float(getattr(plan_cp, "PatientSupportAngle", 0.0))
    cp.ScanningSpotSize            = list(getattr(plan_cp, "ScanningSpotSize", [8.0, 8.0]))
    cp.DeliveredMeterset           = float(delivered_mu_before)
    cp.SpecifiedMeterset           = float(plan_cp.CumulativeMetersetWeight)
    return cp


def make_delivery_cp_end(delivered_mu_total):
    """End-of-layer marker CP."""
    cp = Dataset()
    cp.NumberOfScanSpotPositions = 1
    cp.ScanSpotPositionMap       = [0.0, 0.0]
    cp.ScanSpotMetersetWeights   = [0.0]
    cp.NumberOfPaintings         = 1
    cp.DeliveredMeterset         = float(delivered_mu_total)
    cp.SpecifiedMeterset         = float(delivered_mu_total)
    return cp


def make_treatment_beam(plan_beam, sigma, mu_noise):
    b = Dataset()
    b.ReferencedBeamNumber      = int(plan_beam.BeamNumber)
    b.BeamName                  = str(plan_beam.BeamName)
    b.TreatmentDeliveryType     = "TREATMENT"
    b.CurrentFractionNumber     = args.fraction

    plan_cps = list(plan_beam.IonControlPointSequence)
    del_cps  = []
    cumulative = 0.0

    for idx, plan_cp in enumerate(plan_cps):
        if idx % 2 != 0:
            # end-of-layer marker
            del_cps.append(make_delivery_cp_end(cumulative))
            continue
        dcp = make_delivery_cp(plan_cp, sigma, mu_noise, cumulative)
        cumulative += sum(dcp.ScanSpotMetersetWeights)
        del_cps.append(dcp)

    b.IonControlPointDeliverySequence = Sequence(del_cps)
    b.NumberOfControlPoints           = len(del_cps)
    b.FinalCumulativeMetersetWeight   = float(plan_beam.FinalCumulativeMetersetWeight)
    b.DeliveredPrimaryMeterset        = round(cumulative, 3)
    b.SpecifiedPrimaryMeterset        = float(plan_beam.FinalCumulativeMetersetWeight)
    b.TreatmentTerminationStatus      = "NORMAL"
    b.TreatmentVerificationStatus     = "VERIFIED"
    b.NumberOfBlocks                  = 0
    b.NumberOfBoli                    = 0
    b.NumberOfCompensators            = 0
    b.NumberOfRangeModulators         = 0
    b.NumberOfRangeShifters           = 0
    b.NumberOfLateralSpreadingDevices = 0
    return b


# ── Build treatment beams ─────────────────────────────────────────────────────
treatment_beams = [
    make_treatment_beam(pb, args.sigma, args.mu_noise)
    for pb in plan_beams
]

# ── Main dataset ──────────────────────────────────────────────────────────────
now     = datetime.datetime.now()
sop_uid = generate_uid()

file_meta = FileMetaDataset()
file_meta.MediaStorageSOPClassUID    = "1.2.840.10008.5.1.4.1.1.481.9"
file_meta.MediaStorageSOPInstanceUID = sop_uid
file_meta.TransferSyntaxUID          = ExplicitVRLittleEndian

ds = FileDataset(args.out, {}, file_meta=file_meta, preamble=b"\x00" * 128)
ds.is_implicit_VR  = False
ds.is_little_endian = True

ds.SpecificCharacterSet   = "ISO_IR 192"
ds.SOPClassUID            = "1.2.840.10008.5.1.4.1.1.481.9"
ds.SOPInstanceUID         = sop_uid
ds.StudyDate              = now.strftime("%Y%m%d")
ds.StudyTime              = now.strftime("%H%M%S.%f")
ds.TreatmentDate          = now.strftime("%Y%m%d")
ds.TreatmentTime          = now.strftime("%H%M%S.%f")
ds.Modality               = "RTRECORD"
ds.Manufacturer           = "SyntheticGen"
ds.ManufacturerModelName  = "make_sample_rtrecord"
ds.SoftwareVersions       = "1.0"
ds.StudyInstanceUID       = str(plan_ds.StudyInstanceUID)  # same study
ds.SeriesInstanceUID      = generate_uid()
ds.StudyID                = str(getattr(plan_ds, "StudyID", "PROSTATE01"))
ds.SeriesNumber           = "2"
ds.InstanceNumber         = str(args.fraction)

ds.PatientName            = plan_ds.PatientName
ds.PatientID              = plan_ds.PatientID
ds.PatientBirthDate       = getattr(plan_ds, "PatientBirthDate", "")
ds.PatientSex             = getattr(plan_ds, "PatientSex", "")

# Reference the plan
ref_plan = Dataset()
ref_plan.ReferencedSOPClassUID    = str(plan_ds.SOPClassUID)
ref_plan.ReferencedSOPInstanceUID = plan_sop_uid
ds.ReferencedRTPlanSequence = Sequence([ref_plan])

ds.CurrentFractionNumber  = args.fraction
ds.TreatmentSessionType   = "CONTINUATION" if args.fraction > 1 else "FIRST"

ds.TreatmentSessionIonBeamSequence = Sequence(treatment_beams)

pydicom.dcmwrite(args.out, ds)

print(f"Saved: {args.out}")
print(f"  Fraction : {args.fraction}  ({'QA' if args.fraction == 0 else f'Fx{args.fraction}'})")
print(f"  Plan ref : {args.plan}")
print(f"  Pos σ    : {args.sigma} mm  |  MU noise: {args.mu_noise*100:.1f}%")
print(f"  Beams    : {len(treatment_beams)}")
for b in treatment_beams:
    n_layers = len(b.IonControlPointDeliverySequence) // 2
    print(f"    {b.BeamName:20s}  {n_layers} layers  "
          f"MU delivered={b.DeliveredPrimaryMeterset:.1f}")
