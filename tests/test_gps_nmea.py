"""GPS NMEA parsing + acquisition status.

The GSV cases use the exact sentences a u-blox 7 streamed while cold-
starting with weak indoor signal (7 satellites in view, no fix) — the
real-world state the location panel needs to report as "acquiring"
rather than "no GPS".
"""
from solar_monitor.gps.nmea import parse_gsv, parse_rmc
from solar_monitor.gps.service import GpsService


# ---- parse_gsv ----

def test_parse_gsv_in_view_and_snrs():
    g = parse_gsv("$GPGSV,2,1,07,02,,,19,06,,,23,08,,,22,10,,,22*79")
    assert g == {"talker": "GP", "msg_num": 1, "total_in_view": 7,
                 "snrs": [19, 23, 22, 22]}


def test_parse_gsv_second_sentence_partial_block():
    # Burst sentence 2 of 2 with only 3 satellites → 3 SNRs.
    g = parse_gsv("$GPGSV,2,2,07,17,,,18,31,,,21,32,,,13*73")
    assert g["msg_num"] == 2 and g["total_in_view"] == 7
    assert g["snrs"] == [18, 21, 13]


def test_parse_gsv_blank_snr_fields_omitted():
    # A satellite detected but not tracked has a blank SNR field.
    g = parse_gsv("$GPGSV,1,1,01,05,12,180,*7A")
    assert g["total_in_view"] == 1 and g["snrs"] == []


def test_parse_gsv_accepts_multiconstellation_talkers():
    assert parse_gsv("$GLGSV,1,1,03,65,,,30,66,,,28,72,,,25*60")["talker"] == "GL"
    assert parse_gsv("$GNGSV,1,1,00*64")["talker"] == "GN"


def test_parse_gsv_rejects_non_gsv():
    assert parse_gsv("$GPRMC,231037.00,V,,,,,,,030626,,,N*78") is None
    assert parse_gsv("") is None
    assert parse_gsv("garbage") is None


# ---- RMC void vs active (acquisition boundary) ----

def test_parse_rmc_void_is_no_fix():
    # The cold-start RMC: status V (void) → no fix.
    assert parse_rmc("$GPRMC,231037.00,V,,,,,,,030626,,,N*78") is None


# ---- GpsService acquisition status ----

def _svc():
    async def _noop(lat, lon):  # on_significant_move stub
        return None
    return GpsService(port="/dev/ttyACM0", baudrate=9600, on_significant_move=_noop)


def test_status_reports_acquiring_from_gsv_with_no_fix():
    svc = _svc()
    # Feed the real two-sentence burst: 7 in view, best SNR 23, no fix.
    for line in ("$GPGSV,2,1,07,02,,,19,06,,,23,08,,,22,10,,,22*79",
                 "$GPGSV,2,2,07,17,,,18,31,,,21,32,,,13*73"):
        svc._record_gsv(parse_gsv(line))
    st = svc.get_status()
    assert st["satellites_in_view"] == 7
    assert st["best_snr_dbhz"] == 23
    assert st["has_fix"] is False
    assert st["acquiring"] is True


def test_status_no_satellites_is_not_acquiring():
    svc = _svc()
    st = svc.get_status()
    assert st["satellites_in_view"] == 0
    assert st["best_snr_dbhz"] is None
    assert st["acquiring"] is False


def test_status_sums_across_constellations():
    svc = _svc()
    svc._record_gsv(parse_gsv("$GPGSV,1,1,04,02,,,19,06,,,23,08,,,22,10,,,20*7C"))
    svc._record_gsv(parse_gsv("$GLGSV,1,1,03,65,,,30,66,,,28,72,,,25*60"))
    st = svc.get_status()
    assert st["satellites_in_view"] == 7        # 4 GPS + 3 GLONASS
    assert st["best_snr_dbhz"] == 30            # strongest across both
