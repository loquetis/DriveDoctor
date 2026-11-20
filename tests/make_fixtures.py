"""Canned smartctl -j / PowerShell JSON (shapes follow real smartctl 7.x output) for the box tests."""
import json, os, re, sys
out = sys.argv[1]; os.makedirs(out, exist_ok=True)
def w(name, obj): json.dump(obj, open(os.path.join(out, name), "w"))
def fn(dev): return "smart_%s.json" % re.sub(r"[^A-Za-z0-9]", "_", dev)
def attr(i, name, value, worst, thresh, raw, flags="PO--CK", failed=""):
    return {"id": i, "name": name, "value": value, "worst": worst, "thresh": thresh, "when_failed": failed, "flags": {"string": flags, "prefailure": "P" in flags}, "raw": {"value": raw, "string": str(raw)}}
devs = [{"name": "/dev/nvme0", "type": "nvme", "protocol": "NVMe"}, {"name": "/dev/sda", "type": "sat", "protocol": "ATA"}, {"name": "/dev/sdb", "type": "sat", "protocol": "ATA"},
        {"name": "/dev/sdc", "type": "sat", "protocol": "ATA"}, {"name": "/dev/sdd", "type": "scsi", "protocol": "SCSI"}]
w("scan.json", {"devices": devs})
w(fn("/dev/nvme0"), {"smartctl": {"exit_status": 0}, "device": devs[0], "model_name": "Samsung SSD 980 PRO 1TB", "serial_number": "S5GXNX0R123456A", "firmware_version": "5B2QGXA7",
    "nvme_total_capacity": 1000204886016, "user_capacity": {"bytes": 1000204886016}, "smart_status": {"passed": True},
    "nvme_smart_health_information_log": {"critical_warning": 0, "temperature": 41, "available_spare": 100, "available_spare_threshold": 10, "percentage_used": 4, "data_units_written": 21_000_000,
                                          "power_cycles": 612, "power_on_hours": 4310, "unsafe_shutdowns": 17, "media_errors": 0, "num_err_log_entries": 0, "warning_temp_time": 0, "critical_comp_time": 0},
    "temperature": {"current": 41}, "power_on_time": {"hours": 4310}})
w(fn("/dev/sda"), {"smartctl": {"exit_status": 0}, "device": devs[1], "model_name": "CT500MX500SSD1", "serial_number": "2145E5A1B2C3", "firmware_version": "M3CR033", "rotation_rate": 0,
    "user_capacity": {"bytes": 500107862016}, "smart_status": {"passed": True}, "temperature": {"current": 33}, "power_on_time": {"hours": 18250},
    "ata_smart_attributes": {"table": [attr(5, "Reallocated_Sector_Ct", 100, 100, 10, 0), attr(9, "Power_On_Hours", 100, 100, 0, 18250, "-O--CK"), attr(177, "Wear_Leveling_Count", 100, 100, 0, 0),
                                       attr(197, "Current_Pending_Sector", 100, 100, 0, 0, "-O--CK"), attr(199, "UDMA_CRC_Error_Count", 100, 100, 0, 0, "-OSRCK"),
                                       attr(202, "Percent_Lifetime_Remain", 91, 91, 1, 9), attr(194, "Temperature_Celsius", 67, 52, 0, 33, "-O---K")]}})
w(fn("/dev/sdb"), {"smartctl": {"exit_status": 4}, "device": devs[2], "model_name": "WDC WD40EZRZ-00GXCB0", "serial_number": "WD-WCC7K1234567", "firmware_version": "80.00A80", "rotation_rate": 5400,
    "user_capacity": {"bytes": 4000787030016}, "smart_status": {"passed": True}, "temperature": {"current": 52}, "power_on_time": {"hours": 61000},
    "ata_smart_attributes": {"table": [attr(5, "Reallocated_Sector_Ct", 190, 190, 140, 24), attr(197, "Current_Pending_Sector", 198, 198, 0, 8, "-O--CK"), attr(198, "Offline_Uncorrectable", 200, 200, 0, 0, "----CK"),
                                       attr(199, "UDMA_CRC_Error_Count", 200, 200, 0, 3, "-O--CK"), attr(9, "Power_On_Hours", 33, 33, 0, 61000, "-O--CK")]},
    "ata_smart_self_test_log": {"standard": {"table": [{"type": {"value": 1, "string": "Short offline"}, "status": {"value": 0, "string": "Completed without error", "passed": True}, "lifetime_hours": 60990}]}}})
w(fn("/dev/sdc"), {"smartctl": {"exit_status": 8}, "device": devs[3], "model_name": "ST2000DM008-2FR102", "serial_number": "ZFL1ABCD", "firmware_version": "0001", "rotation_rate": 7200,
    "user_capacity": {"bytes": 2000398934016}, "smart_status": {"passed": False}, "temperature": {"current": 61}, "power_on_time": {"hours": 30000},
    "ata_smart_attributes": {"table": [attr(5, "Reallocated_Sector_Ct", 20, 20, 36, 3800, failed="NOW"), attr(197, "Current_Pending_Sector", 100, 100, 0, 120, "-O--C-")]}})
w(fn("/dev/sdd"), {"smartctl": {"exit_status": 2, "messages": [{"string": "/dev/sdd: Unknown USB bridge [0x0bda:0x9210 (0x0100)]", "severity": "error"}]}, "device": devs[4]})
w("ps.json", [
  {"Name": "Samsung SSD 980 PRO 1TB", "Model": "Samsung SSD 980 PRO 1TB", "Serial": "0025_38B5_3101_8B3D.", "MediaType": "SSD", "BusType": "NVMe", "Size": 1000204886016, "Health": "Healthy", "Operational": "OK", "Temperature": 41, "Wear": 4, "PowerOnHours": 4310},
  {"Name": "CT500MX500SSD1", "Model": "CT500MX500SSD1", "Serial": "2145E5A1B2C3", "MediaType": "SSD", "BusType": "SATA", "Size": 500107862016, "Health": "Healthy", "Operational": "OK", "Temperature": 33},
  {"Name": "WDC WD40EZRZ-00GXCB0", "Model": "WDC WD40EZRZ-00GXCB0", "Serial": "WD-WCC7K1234567", "MediaType": "HDD", "BusType": "SATA", "Size": 4000787030016, "Health": "Warning", "Operational": "OK"},
  {"Name": "ST2000DM008-2FR102", "Model": "ST2000DM008-2FR102", "Serial": "ZFL1ABCD", "MediaType": "HDD", "BusType": "SATA", "Size": 2000398934016, "Health": "Unhealthy", "Operational": "Predictive Failure"},
  {"Name": "Generic USB Drive", "Model": "Generic USB Drive", "Serial": "", "MediaType": "Unspecified", "BusType": "USB", "Size": 64021856256, "Health": "Healthy", "Operational": "OK"}])
