from __future__ import annotations

import argparse
import html
import json
import logging
import math
import os
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from string import Template
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
VALID_FILTERS = {"all", "cargo", "crew"}
COOKIE_NAME = "strange_crew_watchlist"
CACHE_TTL_SECONDS = int(os.environ.get("STRANGE_CREW_TTL", "20"))
ROUTE_TTL_SECONDS = 900
ON_DEMAND_TTL_SECONDS = 300
USER_AGENT = "StrangeCrewFlightTracker/4.0 (Public ADS-B OSINT Viewer)"
FLIGHT_ID_RE = re.compile(r"^[A-Z0-9._-]{2,16}$")

TEMPLATE = Template((ROOT / "index.html").read_text(encoding="utf-8"))

# VIP, Presidential, "Doomsday" & Special Mission Tail/Hex Dossier
# Keyed by lowercase 6-char ICAO hex OR normalized uppercase registration/serial
VIP_DOSSIER_ENTRIES: tuple[dict, ...] = (
    {
        "keys": ("adfdf8", "82-8000", "828000", "28000"),
        "title": "VC-25A Air Force One (Tail 82-8000)",
        "unit": "Presidential Airlift Group · 89th Airlift Wing",
        "home_base": "Joint Base Andrews, MD (KADW)",
        "type": "crew",
        "badge": "VC-25A / SAM VIP",
        "oddity": 99,
        "aboard": "Presidential Flight Crew, Secret Service & Senior White House Staff",
        "mission": "Heavily modified Boeing 747-200B configured as an airborne White House with EMP hardening, aerial refueling capability, and secure global SATCOM.",
    },
    {
        "keys": ("adfdf9", "92-9000", "929000", "29000"),
        "title": "VC-25A Air Force One (Tail 92-9000)",
        "unit": "Presidential Airlift Group · 89th Airlift Wing",
        "home_base": "Joint Base Andrews, MD (KADW)",
        "type": "crew",
        "badge": "VC-25A / SAM VIP",
        "oddity": 99,
        "aboard": "Presidential Flight Crew, Secret Service & Senior White House Staff",
        "mission": "Heavily modified Boeing 747-200B configured as an airborne White House with EMP hardening, aerial refueling capability, and secure global SATCOM.",
    },
    {
        "keys": ("adfeb3", "73-1676", "731676", "31676"),
        "title": "E-4B Nightwatch 'Doomsday Plane' (73-1676)",
        "unit": "595th Command & Control Group · 1st ACCS",
        "home_base": "Offutt AFB, NE (KOFF)",
        "type": "crew",
        "badge": "E-4B NIGHTWATCH",
        "oddity": 99,
        "aboard": "Joint Operations Battle Staff, Flight Crew & SecDef Command Team",
        "mission": "National Airborne Operations Center (NAOC): EMP-shielded 747-200 command post capable of directing U.S. nuclear forces and submerged submarines via VLF trailing wire.",
    },
    {
        "keys": ("adfeb4", "73-1677", "731677", "31677"),
        "title": "E-4B Nightwatch 'Doomsday Plane' (73-1677)",
        "unit": "595th Command & Control Group · 1st ACCS",
        "home_base": "Offutt AFB, NE (KOFF)",
        "type": "crew",
        "badge": "E-4B NIGHTWATCH",
        "oddity": 99,
        "aboard": "Joint Operations Battle Staff, Flight Crew & SecDef Command Team",
        "mission": "National Airborne Operations Center (NAOC): EMP-shielded 747-200 command post capable of directing U.S. nuclear forces and submerged submarines via VLF trailing wire.",
    },
    {
        "keys": ("adfeb5", "74-0787", "740787", "40787"),
        "title": "E-4B Nightwatch 'Doomsday Plane' (74-0787)",
        "unit": "595th Command & Control Group · 1st ACCS",
        "home_base": "Offutt AFB, NE (KOFF)",
        "type": "crew",
        "badge": "E-4B NIGHTWATCH",
        "oddity": 99,
        "aboard": "Joint Operations Battle Staff, Flight Crew & SecDef Command Team",
        "mission": "National Airborne Operations Center (NAOC): EMP-shielded 747-200 command post capable of directing U.S. nuclear forces and submerged submarines via VLF trailing wire.",
    },
    {
        "keys": ("adfeb6", "75-0125", "750125", "50125"),
        "title": "E-4B Nightwatch 'Doomsday Plane' (75-0125)",
        "unit": "595th Command & Control Group · 1st ACCS",
        "home_base": "Offutt AFB, NE (KOFF)",
        "type": "crew",
        "badge": "E-4B NIGHTWATCH",
        "oddity": 99,
        "aboard": "Joint Operations Battle Staff, Flight Crew & SecDef Command Team",
        "mission": "National Airborne Operations Center (NAOC): EMP-shielded 747-200 command post capable of directing U.S. nuclear forces and submerged submarines via VLF trailing wire.",
    },
    {
        "keys": (
            "adfeb7", "98-0001", "980001", "80001",
            "adfeb8", "98-0002", "980002", "80002",
            "adfeb9", "99-0003", "990003", "90003",
            "adfeba", "99-0004", "990004", "90004",
            "ae4ae6", "09-0015", "090015", "90015",
            "ae4ae8", "09-0016", "090016", "90016",
            "ae4aea", "09-0017", "090017", "90017",
            "ae4aec", "09-0018", "090018", "90018",
        ),
        "title": "C-32A Air Force Two / Executive VIP Jet",
        "unit": "1st Airlift Squadron · 89th Airlift Wing",
        "home_base": "Joint Base Andrews, MD (KADW)",
        "type": "crew",
        "badge": "C-32A / AF2 VIP",
        "oddity": 93,
        "aboard": "VIP Aircrew + Vice President, Secretary of State or Cabinet Delegation",
        "mission": "Modified Boeing 757-200 executive transport fitted with global encrypted voice/data suites for senior U.S. government leaders.",
    },
    {
        "keys": (
            "ae0411", "162782", "ae0412", "162783", "ae0413", "162784",
            "ae0414", "163918", "ae0415", "163919", "ae0416", "163920",
            "ae0417", "164386", "ae0418", "164387", "ae0419", "164388",
            "ae041a", "164404", "ae041b", "164405", "ae041c", "164406",
            "ae041d", "164407", "ae041e", "164408", "ae041f", "164409",
            "ae0420", "164410",
        ),
        "title": "E-6B Mercury TACAMO / Looking Glass Relay",
        "unit": "Strategic Communications Wing ONE (SCW-1) · VQ-3 / VQ-4",
        "home_base": "Tinker AFB, OK (KTIK)",
        "type": "crew",
        "badge": "E-6B TACAMO",
        "oddity": 96,
        "aboard": "22-Person Naval Flight & Battle Staff + Airborne Launch Control System",
        "mission": "Nuclear command relay that reels out a 5-mile dual VLF trailing-wire antenna to transmit Emergency Action Messages to ballistic missile submarines.",
    },
    {
        "keys": ("ae048d", "64-14836", "ae0941", "64-14831", "ae0493", "64-14829"),
        "title": "WC-135R Constant Phoenix ('Nuke Sniffer')",
        "unit": "45th Reconnaissance Squadron · 55th Wing",
        "home_base": "Offutt AFB, NE (KOFF)",
        "type": "mixed",
        "badge": "WC-135 SNIFFER",
        "oddity": 97,
        "aboard": "AFTAC Nuclear Technicians + Whole-Air & Particulate Filter Pods",
        "mission": "Atmospheric radiological collection aircraft sampling the upper atmosphere for nuclear detonation debris and radioactive isotopes.",
    },
    {
        "keys": ("ae01d1", "61-2662", "ae01d2", "61-2663", "ae01d3", "62-4128"),
        "title": "RC-135S Cobra Ball Ballistic Tracker",
        "unit": "45th Reconnaissance Squadron · 55th Wing",
        "home_base": "Offutt AFB, NE (KOFF)",
        "type": "crew",
        "badge": "COBRA BALL",
        "oddity": 96,
        "aboard": "Optical/IR Sensor Analysts + Medium-Wave IR & Telemetry Trackers",
        "mission": "Specialized reconnaissance airframe tracking ballistic missile re-entry vehicles and treaty-verification telemetry.",
    },
    {
        "keys": (
            "a3ec1f", "N365SR", "a822da", "N623RA", "a3258c", "N319BD",
            "a2b320", "N273RH", "a2ec9b", "N288DP", "ac948a", "N910CB", "a89bb0", "N654BA",
        ),
        "title": "JANET Classified Test-Range Shuttle",
        "unit": "USAF / Department of the Air Force Special Projects",
        "home_base": "Harry Reid Int'l Restricted Terminal, Las Vegas (KLAS)",
        "type": "crew",
        "badge": "CLASSIFIED SHUTTLE",
        "oddity": 97,
        "aboard": "Cleared Scientists, Defense Engineers & Range Specialists",
        "mission": "Unmarked terminal commuter shuttle operating restricted flights between Las Vegas and Groom Lake / Tonopah Test Range.",
    },
    {
        "keys": ("ad43a6", "N941NA"),
        "title": "NASA B377SGT Super Guppy Turbine",
        "unit": "NASA Johnson Space Center Aircraft Operations",
        "home_base": "Ellington Field, Houston, TX (KEFD)",
        "type": "cargo",
        "badge": "NASA SUPER GUPPY",
        "oddity": 98,
        "aboard": "NASA Flight Crew + Spacecraft Modules / Rocket Fairings",
        "mission": "One-of-a-kind hinged-nose outsize transporter built to haul complete spacecraft structures and rocket stages across the U.S.",
    },
    {
        "keys": ("acd53d", "N926NA", "acda48", "N927NA"),
        "title": "NASA WB-57F High-Altitude Research Jet",
        "unit": "NASA Johnson Space Center High-Altitude Research Program",
        "home_base": "Ellington Field, Houston, TX (KEFD)",
        "type": "mixed",
        "badge": "NASA WB-57F",
        "oddity": 97,
        "aboard": "Pilot + Sensor Equipment Operator & Stratospheric Payload Pallet",
        "mission": "Extreme-altitude twin-jet capable of cruising above 60,000 ft for rocket launch optical tracking, atmospheric sampling, and sensor testing.",
    },
    {
        "keys": ("aa3418", "N706NA", "ab0186", "N809NA"),
        "title": "NASA ER-2 Earth Resources Stratospheric Lab",
        "unit": "NASA Armstrong Flight Research Center",
        "home_base": "Palmdale / Armstrong Bldg 703, CA (KPMD)",
        "type": "mixed",
        "badge": "NASA ER-2",
        "oddity": 97,
        "aboard": "Pressure-Suited Pilot + Modular Wing & Nose Earth-Science Pods",
        "mission": "Civilian research derivative of the U-2 operating at 65,000–70,000 ft as a satellite calibration and atmospheric observatory.",
    },
)

VIP_LOOKUP: dict[str, dict] = {}
for _entry in VIP_DOSSIER_ENTRIES:
    for _k in _entry["keys"]:
        VIP_LOOKUP[_k.strip().lower()] = _entry
        VIP_LOOKUP[re.sub(r"[^A-Z0-9]", "", _k.strip().upper())] = _entry

# Special Use Airspace (SUA) & Restricted Range Polygons ([lat, lon] vertices)
SPECIAL_USE_AIRSPACES: tuple[dict, ...] = (
    {
        "id": "R-4808N",
        "name": "Nevada Test & Training Range / Groom Lake (R-4808N)",
        "kind": "RESTRICTED",
        "color": "#f43f5e",
        "coords": [
            [37.88, -116.95],
            [37.88, -115.35],
            [36.75, -115.35],
            [36.75, -116.45],
            [37.15, -116.95],
        ],
    },
    {
        "id": "R-4807",
        "name": "Tonopah Electronic Combat Range (R-4807)",
        "kind": "RESTRICTED",
        "color": "#fb7185",
        "coords": [
            [38.30, -117.15],
            [38.30, -115.95],
            [37.88, -115.95],
            [37.88, -117.15],
        ],
    },
    {
        "id": "R-2508",
        "name": "Edwards AFB / China Lake R-2508 Complex",
        "kind": "TEST RANGE",
        "color": "#f59e0b",
        "coords": [
            [36.55, -118.25],
            [36.55, -116.75],
            [34.72, -116.75],
            [34.72, -118.35],
            [35.55, -118.45],
        ],
    },
    {
        "id": "DC-SFRA",
        "name": "Washington D.C. SFRA / P-56 National Defense Airspace",
        "kind": "NATIONAL DEFENSE",
        "color": "#ef4444",
        "coords": [
            [39.39, -77.15],
            [39.32, -76.55],
            [38.89, -76.39],
            [38.48, -76.58],
            [38.39, -77.04],
            [38.48, -77.50],
            [38.89, -77.68],
            [39.32, -77.52],
        ],
    },
    {
        "id": "WSMR-5107",
        "name": "White Sands Missile Range (R-5107)",
        "kind": "MISSILE RANGE",
        "color": "#f59e0b",
        "coords": [
            [33.95, -106.85],
            [33.95, -105.85],
            [32.25, -105.85],
            [32.25, -106.85],
        ],
    },
    {
        "id": "R-2301",
        "name": "Barry M. Goldwater Tactical Range (R-2301)",
        "kind": "TACTICAL RANGE",
        "color": "#f59e0b",
        "coords": [
            [33.05, -114.45],
            [33.05, -112.65],
            [32.05, -112.65],
            [32.15, -114.45],
        ],
    },
    {
        "id": "EGLIN-W151",
        "name": "Eglin Gulf Test & Training Range (W-151)",
        "kind": "WARNING AREA",
        "color": "#38bdf8",
        "coords": [
            [30.45, -87.35],
            [30.35, -85.45],
            [28.55, -85.35],
            [28.55, -87.45],
        ],
    },
)

# Callsign prefix -> (operator, category, headline, aboard_summary, mission_detail, base_oddity, badge)
CALLSIGN_PROFILES: dict[str, tuple[str, str, str, str, str, int, str]] = {
    "JANET": (
        "USAF / EG&G Special Projects",
        "crew",
        "Classified Nevada Test Range Shuttle",
        "Cleared Scientists, Engineers & Defense Contractors",
        "Unmarked terminal shuttle operating restricted flights between Las Vegas and Groom Lake / Tonopah Test Range.",
        96,
        "CLASSIFIED SHUTTLE",
    ),
    "WWW": (
        "USAF / Special Projects Shuttle",
        "crew",
        "Restricted Test-Site Commuter Flight",
        "Cleared Range Personnel & Technical Specialists",
        "Operating under restricted callsign protocols serving remote desert test facilities.",
        92,
        "RESTRICTED OPS",
    ),
    "NOAA": (
        "NOAA Aircraft Operations Center",
        "mixed",
        "Atmospheric & Hurricane Research Mission",
        "Flight Meteorologists + GPS Dropsondes & Doppler Radar",
        "Scientists and flight crew deploying atmospheric dropsonde probes and mapping storm structures.",
        91,
        "WEATHER RECON",
    ),
    "TEAL": (
        "53rd Weather Reconnaissance Squadron",
        "mixed",
        "Hurricane Hunter Weather Reconnaissance",
        "Weather Recon Aircrew + Dropsonde Dispensers & Sensors",
        "Penetrating tropical cyclones and atmospheric rivers to measure barometric pressure and wind shear.",
        90,
        "HURRICANE HUNTER",
    ),
    "NASA": (
        "NASA Airborne Science Program",
        "mixed",
        "Airborne Earth-Science & Sensor Laboratory",
        "Research Scientists + LIDAR, Spectrometers & Telescopes",
        "Operating high-altitude Earth-observation instruments, atmospheric samplers, or aerospace test hardware.",
        93,
        "AIRBORNE SCIENCE",
    ),
    "ADB": (
        "Antonov Airlines",
        "cargo",
        "Super-Heavy Outsize Cargo Airlift",
        "Load Engineers + Satellites, Turbines or Locomotives",
        "Nose-loading An-124 super-transporter carrying single-piece industrial or aerospace loads too huge for standard jets.",
        95,
        "OUTSIZE RAMP",
    ),
    "VDA": (
        "Volga-Dnepr Airlines",
        "cargo",
        "Outsize Heavy Freight Charter",
        "Heavy Load Crew + Aerospace Modules or Oilfield Machinery",
        "Ramp-loading heavy transport equipped with internal overhead gantry cranes.",
        90,
        "OUTSIZE RAMP",
    ),
    "BGA": (
        "Airbus Transport International",
        "cargo",
        "Beluga Oversized Aerospace Airlift",
        "Flight Crew + Complete Aircraft Wings & Fuselage Sections",
        "Bulbous upper-deck transporter shuttling major sub-assemblies between European aerospace plants.",
        95,
        "BELUGA TRANSPORTER",
    ),
    "FORTE": (
        "High-Altitude Reconnaissance Detachment",
        "crew",
        "Stratospheric Long-Endurance Surveillance",
        "Remote Sensor Crew + Multi-Spectral ISR Radar Suite",
        "Operating above 50,000 ft on a 24+ hour endurance orbit with wide-area surveillance sensors.",
        94,
        "STRATOSPHERIC ISR",
    ),
    "DRAGON": (
        "9th Reconnaissance Wing (U-2S)",
        "crew",
        "Near-Space Stratospheric Reconnaissance",
        "Pressure-Suited Pilot + High-Altitude Optical/SIGINT Pod",
        "Flying at the edge of space above 60,000 ft with a pilot in a full-pressure astronaut suit.",
        97,
        "EDGE OF SPACE",
    ),
    "COBRA": (
        "USAF Special Reconnaissance",
        "crew",
        "Ballistic & Technical Telemetry Observation",
        "Technical Mission Crew + Phased-Array & Optical Trackers",
        "Specialized observation aircraft tracking missile tests or treaty-verification telemetry.",
        91,
        "TELEMETRY OPS",
    ),
    "JAKE": (
        "USAF 55th Reconnaissance Wing",
        "crew",
        "Airborne Signals & Electronic Intelligence",
        "Cryptologic Linguists + SIGINT Collection Racks",
        "Specialist sensor operators collecting and geolocating electronic and communications emissions.",
        88,
        "SIGINT COLLECTION",
    ),
    "HOMER": (
        "USAF Reconnaissance Operations",
        "crew",
        "Standoff Electronic Surveillance Sortie",
        "Airborne Intelligence Crew + ELINT Sensor Array",
        "Conducting long-range electronic surveillance and maritime border monitoring.",
        87,
        "ELINT ORBIT",
    ),
    "GORDO": (
        "USAF National Airborne Operations Center",
        "crew",
        "E-4B Nightwatch Airborne Command Post",
        "Joint Battle Staff, Flight Crew + EMP-Shielded Comms",
        "Survivable airborne command center carrying senior defense planners and global satellite links.",
        95,
        "COMMAND POST",
    ),
    "TITAN": (
        "USAF Airborne Command Post",
        "crew",
        "Strategic Airborne Command & Control",
        "Battle Staff & Aircrew + Global Communications Suite",
        "High-priority national command and control airborne platform.",
        93,
        "COMMAND POST",
    ),
    "RCH": (
        "USAF Air Mobility Command",
        "cargo",
        "Strategic Heavy Airlift Mission",
        "Loadmasters + Armored Vehicles, Helicopters or Aid Pallets",
        "Carrying outsize defense cargo, rolling stock, or humanitarian relief supplies along global mobility routes.",
        76,
        "HEAVY AIRLIFT",
    ),
    "SAM": (
        "89th Airlift Wing (Special Air Mission)",
        "crew",
        "Diplomatic & Senior Leadership Transport",
        "VIP Aircrew + Cabinet Delegation & Security Detail",
        "Transporting senior government officials or diplomatic delegations with secure satellite comms.",
        86,
        "DIPLOMATIC VIP",
    ),
    "EXEC": (
        "Executive Flight Detachment",
        "crew",
        "Senior Command & Executive Transport",
        "Command Aircrew + Senior Defense Leadership",
        "Dedicated point-to-point transport for senior military or government officials.",
        82,
        "COMMAND FLIGHT",
    ),
    "SPAR": (
        "US Defense Airlift",
        "crew",
        "Special Priority Attaché & Official Flight",
        "Priority Aircrew + Diplomatic Attachés & Staff",
        "Special priority airlift supporting diplomatic missions and official travel.",
        80,
        "PRIORITY AIRLIFT",
    ),
    "VENUS": (
        "89th Airlift Wing",
        "crew",
        "Special Air Mission Readiness Sortie",
        "VIP Transport Pilots, Cabin Crew & Flight Evaluators",
        "Executive aircrew proficiency and transport operations out of Joint Base Andrews.",
        76,
        "VIP AIRCREW",
    ),
    "CNV": (
        "US Navy Fleet Logistics Support",
        "cargo",
        "Naval Carrier-Onboard / Fleet Logistics Run",
        "Navy Logistics Crew + Jet Engines, Ship Parts & Mail",
        "Delivering critical fleet hardware and naval specialists between coastal air stations.",
        75,
        "FLEET LOGISTICS",
    ),
    "USCG": (
        "U.S. Coast Guard Aviation",
        "crew",
        "Maritime Search, Rescue & Ice/Border Patrol",
        "Rescue Swimmers, Aircrew + Search Radar & Drop Rafts",
        "Conducting coastal search-and-rescue readiness, marine environmental patrol, or fisheries enforcement.",
        82,
        "COAST GUARD SAR",
    ),
    "KING": (
        "USAF Combat Rescue Squadron",
        "crew",
        "Combat Search & Rescue (CSAR) Mission",
        "Pararescue Jumpers (PJs), Flight Medics & Hoist Gear",
        "Specialized personnel-recovery aircrew equipped for long-range search and medical extraction.",
        87,
        "COMBAT RESCUE",
    ),
    "JOLLY": (
        "USAF Rescue Helicopter Unit",
        "crew",
        "Heavy Rescue Rotorcraft Sortie",
        "Rescue Pilots, Flight Engineers & Pararescue Team",
        "Long-range search, recovery, and tactical medical evacuation rotorcraft mission.",
        86,
        "RESCUE ROTORCRAFT",
    ),
    "PEDRO": (
        "USAF Rescue Detachment",
        "crew",
        "Search & Rescue / Medevac Flight",
        "Flight Medics, Rescue Specialists & ICU Litter Bay",
        "Dedicated personnel recovery and aeromedical evacuation flight crew.",
        85,
        "MEDEVAC RESCUE",
    ),
    "LAGR": (
        "USAF Tanker Task Force",
        "mixed",
        "Aerial Refueling Bridge Mission",
        "Boom Operator Crew + 150,000+ lbs Aviation Jet Fuel",
        "Flying fuel tanker refueling receiver aircraft mid-air along transoceanic corridors.",
        81,
        "AERIAL TANKER",
    ),
    "GOLD": (
        "USAF Tanker Operations",
        "mixed",
        "Coronet Fighter Escort & Tanker Bridge",
        "Tanker Crew + Bulk JP-8 Fuel & Fighter Support Kit",
        "Escorting deploying fighter jets across oceanic crossings with scheduled in-flight refueling.",
        83,
        "CORONET ESCORT",
    ),
    "CLEAN": (
        "USAF Tanker Operations",
        "mixed",
        "Aerial Refueling & Escort Sortie",
        "Refueling Crew + Bulk JP-8 Transfer Tanks",
        "In-flight refueling mission supporting long-range military air movements.",
        80,
        "AERIAL TANKER",
    ),
    "GTI": (
        "Atlas Air",
        "cargo",
        "Outsize Global Charter / Dreamlifter Freighter",
        "Freighter Crew + 787 Wing Sections, Race Cars or Equine Stalls",
        "Widebody 747 freighter operating specialized industrial, aerospace, defense, or live-animal charters.",
        78,
        "HEAVY CHARTER",
    ),
    "CKS": (
        "Kalitta Air",
        "cargo",
        "International Heavy & Live-Cargo Charter",
        "Cargo Crew + Aerospace Hardware, Charter Freight or Equine Stalls",
        "Long-haul 747/777 freighter moving time-critical industrial, automotive, or specialized charter loads.",
        76,
        "HEAVY CHARTER",
    ),
    "KBA": (
        "Kenn Borek Air",
        "mixed",
        "Polar & Remote Expedition Utility Flight",
        "Bush Pilots + Arctic/Antarctic Science Gear & Fuel Drums",
        "World-renowned polar aviation operator flying ski-equipped Twin Otters and Basler DC-3s.",
        92,
        "POLAR EXPEDITION",
    ),
    "CUL": (
        "Coulson Aviation",
        "cargo",
        "Heavy Aerial Firefighting Tanker",
        "Fire Attack Crew + 4,000 Gallons Fire Retardant",
        "Large airtanker modified with high-flow retardant tanks for wildfire suppression.",
        91,
        "FIRE BOMBER",
    ),
    "TNKR": (
        "Aerial Firefighting Air Tanker",
        "cargo",
        "Wildfire Retardant Drop Mission",
        "Aerial Fire Crew + Phos-Chek Retardant Load",
        "Low-altitude wildfire suppression aircraft operating in active fire zones.",
        90,
        "FIRE BOMBER",
    ),
    "LIFE": (
        "Air Ambulance / Medevac",
        "crew",
        "Critical Care Aeromedical Transport",
        "Flight Nurses, Paramedics + ICU Patient Stretcher",
        "Equipped with onboard life-support, ventilators, and cardiac monitors for urgent patient transfer.",
        83,
        "AIR AMBULANCE",
    ),
    "MED": (
        "Medical Evacuation Flight",
        "crew",
        "Emergency Medevac / Donor Organ Flight",
        "Surgical Procurement Team + Transplant Organ Cooler",
        "Time-critical medical evacuation or live donor-organ transport flight.",
        85,
        "ORGAN / MEDEVAC",
    ),
    "RRR": (
        "Royal Air Force Air Transport",
        "cargo",
        "RAF Strategic Airlift & Tanker Sortie",
        "RAF Aircrew + Expeditionary Cargo or Transfer Fuel",
        "UK Royal Air Force strategic mobility, troop transport, or air-to-air refueling flight.",
        78,
        "RAF AIRLIFT",
    ),
    "CFC": (
        "Royal Canadian Air Force",
        "cargo",
        "RCAF Arctic & Strategic Transport",
        "RCAF Crew + Northern Resupply Cargo or Maritime Sensors",
        "Canadian military airlift, high-Arctic resupply, or maritime patrol flight.",
        78,
        "RCAF MISSION",
    ),
    "GAF": (
        "German Air Force (Luftwaffe)",
        "crew",
        "Luftwaffe Special Air Mission",
        "Luftwaffe Crew + Government Delegation or Airlift Cargo",
        "German military transport or official government flight.",
        77,
        "LUFTWAFFE OPS",
    ),
}

OWNER_PATTERNS: tuple[tuple[tuple[str, ...], str, str, str, str, int], ...] = (
    (
        ("TEX SUTTON", "EQUINE", "HORSE", "THOROUGHBRED", "BROOKLEDGE", "DUTTA"),
        "cargo",
        "Equine Air Charter ('Air Horse One')",
        "Grooms, Veterinarian + Custom Thoroughbred Horse Stalls",
        "Specialized live-animal transport fitted with padded air-ride stalls for racehorses and show jumpers.",
        26,
    ),
    (
        ("SPACEX", "SPACE EXPLORATION", "BLUE ORIGIN", "ROCKET LAB", "NORTHROP", "LOCKHEED", "RAYTHEON", "BOEING", "GENERAL ATOMICS", "L3HARRIS", "SIERRA NEVADA"),
        "mixed",
        "Aerospace & Defense Engineering Flight",
        "Flight-Test Engineers + Avionics, Satellites or Telemetry Gear",
        "Corporate aerospace airframe supporting spacecraft recovery, radar calibration, or flight-test telemetry.",
        22,
    ),
    (
        ("NASA", "NOAA", "UCAR", "NCAR", "UNIVERSITY", "INSTITUTE", "RESEARCH", "OBSERVATORY", "WOODS HOLE", "SCRIPPS"),
        "mixed",
        "Scientific & Atmospheric Research Flight",
        "Principal Investigators + Environmental Sensors & Samplers",
        "Academic or federal research airframe collecting atmospheric, oceanographic, or geophysical measurements.",
        24,
    ),
    (
        ("DEPARTMENT OF ENERGY", "NNSA", "ATOMIC", "NUCLEAR", "RADIATION"),
        "mixed",
        "Radiological & Nuclear Aerial Survey",
        "Health Physicists + Gamma-Ray Spectrometer Pods",
        "Equipped with high-sensitivity sodium-iodide radiation detectors for baseline environmental or event mapping.",
        30,
    ),
    (
        ("COULSON", "NEPTUNE AVIATION", "10 TANKER", "AERO-FLITE", "ERICKSON", "CAL FIRE", "FOREST SERVICE", "FIRE"),
        "cargo",
        "Aerial Firefighting & Heavy Lift",
        "Fire Attack Crew + Fire Retardant Tank or Water Snorkel",
        "Modified wildfire suppression airframe or heavy external-load skycrane.",
        24,
    ),
    (
        ("WEATHER MODIFICATION", "CLOUD SEEDING", "METEOROLOGY"),
        "mixed",
        "Weather Modification & Cloud Seeding",
        "Cloud-Seeding Pilot + Wingtip Silver-Iodide Flare Racks",
        "Dispensing glaciogenic or hygroscopic flares into convective clouds to enhance precipitation or suppress hail.",
        28,
    ),
    (
        ("DYNAMIC AVIATION", "FUGRO", "SANDER GEOPHYSICS", "CGG", "SURVEY", "MAPPING", "GEOPHYSICAL", "AERIAL"),
        "mixed",
        "Airborne Geophysical / LIDAR Survey",
        "Sensor Operators + Magnetometer Boom & High-Res LIDAR",
        "Flying systematic low-altitude lawnmower grids for mineral exploration, terrain mapping, or sterile-insect release.",
        21,
    ),
    (
        ("HENDRICK", "JOE GIBBS", "PENSKE", "STEWART-HAAS", "RACING", "MOTORSPORT", "NASCAR", "INDY"),
        "crew",
        "Motorsport Pit Crew & Driver Shuttle",
        "Race Drivers, Pit Crew Chiefs & Telemetry Engineers",
        "Dedicated team aircraft shuttling drivers, engineers, and pit crews directly between track weekends.",
        20,
    ),
    (
        ("ORBIS", "SAMARITAN", "ANGEL FLIGHT", "LIFELINE", "AMBULANCE", "MEDEVAC", "HOSPITAL", "CHILDREN", "MEDICAL", "MERCY"),
        "crew",
        "Humanitarian / Medical Aviation Mission",
        "Medical Specialists + Surgical / ICU Equipment or Relief Cargo",
        "Dedicated medical airlift, flying hospital, or humanitarian disaster-response flight.",
        22,
    ),
)

AIRCRAFT_CATALOG: dict[str, tuple[str, str, str, str, int]] = {
    "A124": ("Antonov An-124 Ruslan", "cargo", "6-Person Flight Crew + 120 Tonnes Outsize Heavy Cargo", "Super-heavy airlifter with internal overhead cranes and kneeling nose loading ramp.", 94),
    "A3ST": ("Airbus A300-600ST Beluga", "cargo", "Flight Crew + Complete Aircraft Fuselage / Satellite Cargo", "Oversized upper-deck aerospace transporter.", 95),
    "A337": ("Airbus A330-743L BelugaXL", "cargo", "Flight Crew + Pair of A350 Wings / Outsize Aerospace Load", "Next-generation whale-shaped aerospace component freighter.", 95),
    "B377": ("Aero Spacelines Super Guppy", "cargo", "Flight Crew + NASA Spacecraft / Rocket Stage Components", "Bulbous turbine outsize cargo plane operated by NASA for oversized space hardware.", 98),
    "S64": ("Sikorsky S-64 Skycrane", "cargo", "3-Person Crew + 20,000 lb External Sling / Fire Tank", "Exoskeleton heavy-lift flying crane for powerline towers, HVAC units, and wildfire drops.", 92),
    "U2": ("Lockheed U-2S Dragon Lady", "crew", "Pressure-Suited Pilot + Stratospheric Optical/Radar Bay", "High-aspect-ratio reconnaissance aircraft cruising above 60,000 ft.", 97),
    "RQ4": ("Northrop Grumman RQ-4 Global Hawk", "crew", "Unmanned + High-Altitude Persistent Radar Suite", "Autonomous stratospheric surveillance aircraft with a 130-ft wingspan.", 95),
    "R135": ("Boeing RC-135 Rivet Joint", "crew", "25+ Flight & Intel Crew + Signals Intelligence Racks", "Reconnaissance aircraft staffed by onboard linguists and electronic warfare analysts.", 91),
    "E6": ("Boeing E-6B Mercury", "crew", "22-Person Battle Staff + VLF Trailing-Wire Submarine Antenna", "Airborne strategic communications relay that reels out a 5-mile wire antenna in flight.", 95),
    "E4": ("Boeing E-4B Nightwatch", "crew", "Joint Operations Battle Staff + EMP-Shielded Global Comms", "National Airborne Operations Center survivable command post.", 98),
    "E3CF": ("Boeing E-3 Sentry (AWACS)", "crew", "20+ Radar Controllers + 30-ft Rotating Radar Dome", "Airborne warning and control radar surveillance center.", 90),
    "P3": ("Lockheed P-3 / WP-3D Orion", "mixed", "10–18 Crew & Scientists + Dropsondes & Maritime Sensors", "Four-turboprop hurricane hunter and ocean surveillance aircraft.", 88),
    "P8": ("Boeing P-8A Poseidon", "crew", "9-Person Mission Crew + Sonobuoys & Maritime Search Radar", "Anti-submarine warfare and ocean reconnaissance aircraft.", 84),
    "C5M": ("Lockheed C-5M Super Galaxy", "cargo", "7-Person Crew + Massive Outsize Rolling/Aerospace Cargo", "Intercontinental giant strategic airlifter with front and rear cargo ramps.", 89),
    "C17": ("Boeing C-17 Globemaster III", "cargo", "3-Person Crew + Up to 170,900 lbs Outsize Cargo", "Strategic heavy airlifter capable of hauling tanks, helicopters, and palletized freight.", 80),
    "C130": ("Lockheed C-130H Hercules", "cargo", "5-Person Crew + Tactical Pallets, Paratroops or Polar Gear", "Four-turboprop tactical transport for cargo airdrops and austere airstrips.", 77),
    "C30J": ("Lockheed Martin C-130J Super Hercules", "cargo", "3–4 Person Crew + Palletized Cargo or Weather Sensors", "Upgraded tactical airlifter and specialized reconnaissance airframe.", 78),
    "A400": ("Airbus A400M Atlas", "cargo", "3–4 Person Crew + 37 Tonnes Strategic/Tactical Cargo", "Four-turboprop military airlifter bridging tactical and strategic transport.", 80),
    "V22": ("Bell Boeing V-22 Osprey", "mixed", "4-Person Crew + 24 Troops or Internal Expedition Cargo", "Tiltrotor aircraft combining vertical helicopter lift with turboprop cruise speed.", 88),
    "K35R": ("Boeing KC-135R Stratotanker", "mixed", "3-Person Crew + 200,000 lbs Transferable Jet Fuel", "Four-engine aerial refueling tanker and cargo/personnel support jet.", 80),
    "KC46": ("Boeing KC-46A Pegasus", "mixed", "3-Person Crew + Bulk Aviation Fuel & Cargo Pallets", "Next-generation widebody refueling tanker and strategic freighter.", 82),
    "KC10": ("McDonnell Douglas KC-10 Extender", "mixed", "4-Person Crew + 356,000 lbs Transferable Jet Fuel", "Three-engine widebody boom-and-drogue aerial refueling tanker.", 84),
    "DC87": ("Douglas DC-8-72", "mixed", "Flight Crew + Airborne Science Lab or Field Hospital Cargo", "Rare classic four-engine jet still flown by NASA and humanitarian relief organizations.", 92),
    "T38": ("Northrop T-38C Talon", "crew", "2-Person Aircrew (Pilot + Weapon Systems / Test Officer)", "Supersonic twin-jet trainer used for astronaut readiness, test-pilot chase, and adversary training.", 82),
    "DHC6": ("De Havilland DHC-6 Twin Otter", "cargo", "2 Pilots + Expedition Gear & Remote Airstrip Cargo", "STOL bush and polar twin-turboprop built for short gravel, snow, or island strips.", 80),
    "PC12": ("Pilatus PC-12", "cargo", "1–2 Pilots + Aft-Door Utility Cargo or Medevac Stretcher", "Pressurized turboprop with a 53-inch aft cargo door favored for remote airstrips and air ambulance.", 68),
    "PC24": ("Pilatus PC-24", "crew", "2 Pilots + Executive Passengers / Medevac Stretcher", "Twin-engine jet certified for unpaved runways with a rear pallet-sized cargo door.", 72),
    "C208": ("Cessna 208 Caravan", "cargo", "Single Pilot + Belly-Pod & Cabin Feeder Freight / Skydivers", "Rugged utility turboprop hauling remote bush cargo, regional freight, or skydiving teams.", 66),
    "B350": ("Beechcraft King Air 350", "crew", "2 Pilots + Aerial Survey Sensors or Special-Mission Crew", "Twin-turboprop workhorse used for mapping, radiation survey, and government flights.", 69),
    "BE20": ("Beechcraft King Air 200", "crew", "2 Pilots + Medical Stretcher or Survey Crew", "Twin-turboprop commonly configured for air ambulance and aerial mapping.", 66),
    "LJ35": ("Learjet 35A", "crew", "2 Pilots + Medevac ICU Team or Target-Towing Pod", "High-speed light jet frequently tasked for air ambulance and defense support.", 74),
    "H60": ("Sikorsky UH-60 / HH-60 Black Hawk", "crew", "4-Person Crew + Tactical Squad or Medevac Litters", "Twin-engine utility, air-assault, and combat rescue helicopter.", 76),
    "H47": ("Boeing CH-47 Chinook", "cargo", "3–4 Person Crew + Heavy Internal/Sling-Load Cargo", "Tandem-rotor heavy-lift helicopter carrying artillery, vehicles, or firefighting buckets.", 84),
    "EC45": ("Airbus H145 / UH-72A Lakota", "crew", "2 Pilots + Medevac Stretcher or Recon Sensor Crew", "Twin-engine light utility and emergency medical helicopter.", 72),
    "EC35": ("Airbus H135", "crew", "Pilot + Flight Nurse, Paramedic & ICU Bay", "Light twin-engine helicopter commonly flown for critical-care air ambulance.", 73),
    "S92": ("Sikorsky S-92", "crew", "2 Pilots + Up to 19 Offshore Energy Crew / SAR Team", "Heavy twin-engine helicopter built for offshore rig transfers and maritime rescue.", 76),
    "B744": ("Boeing 747-400F / LCF", "cargo", "2–3 Pilots + Outsize Nose-Loaded Freight or Live Stalls", "Nose-loading widebody super-freighter for oversized industrial and charter cargo.", 76),
    "B748": ("Boeing 747-8F", "cargo", "2–3 Pilots + 137 Tonnes Intercontinental Charter Freight", "Flagship heavy cargo jumbo jet.", 74),
    "GLEX": ("Bombardier Global Express / E-11A", "crew", "Flight Crew + Long-Haul Delegation or BACN Relay Suite", "Ultra-long-range heavy business jet and airborne battlefield communications platform.", 74),
    "GLF5": ("Gulfstream G-V / G550", "crew", "Flight Crew + VIP Delegation or Airborne Early Warning", "Ultra-long-range jet widely adapted for diplomatic and special-mission roles.", 72),
    "GLF6": ("Gulfstream G650ER", "crew", "3-Person Crew + Ultra-Long-Range Executive Cabin", "Mach 0.925 flagship business jet capable of 7,500-nautical-mile nonstop legs.", 68),
    "GL7T": ("Bombardier Global 7500", "crew", "3-Person Crew + Four-Zone Intercontinental Suite", "Ultra-long-range flagship business jet.", 68),
    "G280": ("Gulfstream G280", "crew", "2 Pilots + Super-Midsize Executive Cabin", "High-altitude super-midsize jet capable of 4,100-mile legs.", 64),
    "F2TH": ("Dassault Falcon 2000", "crew", "2 Pilots + Transcontinental Corporate Cabin", "Twin-engine large-cabin business jet.", 63),
    "E55P": ("Embraer Phenom 300", "crew", "2 Pilots + Up to 7 Private Charter Passengers", "High-speed light business jet popular for fractional and private charters.", 62),
}

REGISTRATION_PREFIXES: tuple[tuple[str, str], ...] = (
    ("C-", "Canada"),
    ("G-", "United Kingdom"),
    ("D-", "Germany"),
    ("F-", "France"),
    ("HB-", "Switzerland"),
    ("OE-", "Austria"),
    ("PH-", "Netherlands"),
    ("OO-", "Belgium"),
    ("EC-", "Spain"),
    ("I-", "Italy"),
    ("SE-", "Sweden"),
    ("LN-", "Norway"),
    ("OY-", "Denmark"),
    ("OH-", "Finland"),
    ("EI-", "Ireland"),
    ("SP-", "Poland"),
    ("VH-", "Australia"),
    ("ZK-", "New Zealand"),
    ("JA", "Japan"),
    ("HL", "South Korea"),
    ("B-", "China / Taiwan"),
    ("VT-", "India"),
    ("9V-", "Singapore"),
    ("A6-", "United Arab Emirates"),
    ("A7-", "Qatar"),
    ("HZ-", "Saudi Arabia"),
    ("4X-", "Israel"),
    ("TC-", "Turkey"),
    ("UR-", "Ukraine"),
    ("VP-", "UK Overseas / Bermuda / Cayman"),
    ("VQ-", "Bermuda / Cayman Islands"),
    ("XA-", "Mexico"),
    ("XB-", "Mexico"),
    ("XC-", "Mexico"),
    ("PP-", "Brazil"),
    ("PR-", "Brazil"),
    ("PT-", "Brazil"),
    ("LV-", "Argentina"),
    ("CC-", "Chile"),
    ("ZS-", "South Africa"),
    ("N", "United States"),
)

# Strategic Military Airfields, Test Ranges & Major Gateways for Bearing-Cone Corridor Inference
STRATEGIC_BASES: tuple[tuple[str, str, float, float], ...] = (
    # Nevada / California Test & Aerospace Belt
    ("KLAS", "Las Vegas Harry Reid", 36.0840, -115.1537),
    ("KLSV", "Nellis AFB", 36.2362, -115.0343),
    ("KXTA", "Homey / Area 51", 37.2350, -115.8111),
    ("KTNX", "Tonopah Test Range", 37.7986, -116.7810),
    ("KEDW", "Edwards AFB", 34.9054, -117.8837),
    ("KPMD", "Palmdale Plant 42", 34.6294, -118.0846),
    ("KNID", "NAWS China Lake", 35.6856, -117.6920),
    ("KNXP", "Twentynine Palms", 34.2962, -116.1622),
    ("KMHV", "Mojave Air & Space Port", 35.0594, -118.1517),
    ("KVBG", "Vandenberg SFB", 34.7373, -120.5843),
    ("KNKX", "MCAS Miramar", 32.8683, -117.1425),
    ("KNZY", "NAS North Island", 32.6992, -117.2153),
    ("KRIV", "March ARB", 33.8807, -117.2594),
    ("KLAX", "Los Angeles", 33.9425, -118.4081),
    ("KSFO", "San Francisco", 37.6213, -122.3790),
    ("KSUU", "Travis AFB", 38.2627, -121.9275),
    ("KBAB", "Beale AFB", 39.1361, -121.4366),
    ("KNLC", "NAS Lemoore", 36.3330, -119.9521),
    # Pacific Northwest & Alaska
    ("KTCM", "Joint Base Lewis-McChord", 47.1377, -122.4765),
    ("KNUW", "NAS Whidbey Island", 48.3518, -122.6559),
    ("KSEA", "Seattle-Tacoma", 47.4502, -122.3088),
    ("KPAE", "Everett Paine Field", 47.9063, -122.2816),
    ("KSKA", "Fairchild AFB", 47.6151, -117.6558),
    ("KMWH", "Grant County Moses Lake", 47.2077, -119.3202),
    ("PAED", "Joint Base Elmendorf-Richardson", 61.2510, -149.8065),
    ("PAEI", "Eielson AFB", 64.6656, -147.1014),
    ("PAFA", "Fairbanks", 64.8151, -147.8564),
    # Mountain West, Southwest & Plains
    ("KPHX", "Phoenix Sky Harbor", 33.4373, -112.0078),
    ("KLUF", "Luke AFB", 33.5350, -112.3831),
    ("KDMA", "Davis-Monthan AFB", 32.1665, -110.8831),
    ("KNYL", "MCAS Yuma", 32.6566, -114.6060),
    ("KHIF", "Hill AFB", 41.1239, -111.9730),
    ("KDEN", "Denver", 39.8561, -104.6737),
    ("KCOS", "Peterson SFB / Colorado Springs", 38.8058, -104.7008),
    ("KABQ", "Kirtland AFB / Albuquerque", 35.0402, -106.6090),
    ("KHMN", "Holloman AFB / White Sands", 32.8525, -106.1065),
    ("KELP", "El Paso / Biggs AAF", 31.8072, -106.3776),
    ("KRCA", "Ellsworth AFB", 44.1450, -103.1036),
    ("KMIB", "Minot AFB", 48.4156, -101.3580),
    ("KOFF", "Offutt AFB", 41.1183, -95.9125),
    ("KIAB", "McConnell AFB", 37.6219, -97.2682),
    ("KTIK", "Tinker AFB", 35.4147, -97.3866),
    ("KSZL", "Whiteman AFB", 38.7303, -93.5479),
    # Texas & Gulf Coast
    ("KDYS", "Dyess AFB", 32.4208, -99.8546),
    ("KDLF", "Laughlin AFB", 29.3595, -100.7780),
    ("KSKF", "Kelly Field / Lackland", 29.3842, -98.5811),
    ("KDFW", "Dallas-Fort Worth", 32.8998, -97.0403),
    ("KNFW", "NAS JRB Fort Worth", 32.7692, -97.4415),
    ("KGRK", "Robert Gray AAF / Fort Cavazos", 31.0672, -97.8289),
    ("KEFD", "Ellington Field Houston", 29.6073, -95.1588),
    ("KBAD", "Barksdale AFB", 32.5018, -93.6627),
    ("KVPS", "Eglin AFB", 30.4832, -86.5254),
    ("KHRT", "Hurlburt Field", 30.4278, -86.6893),
    ("KPAM", "Tyndall AFB", 30.0696, -85.5754),
    ("KMCF", "MacDill AFB", 27.8493, -82.5212),
    ("KMIA", "Miami", 25.7959, -80.2870),
    # Southeast, Mid-Atlantic & Northeast
    ("KATL", "Atlanta", 33.6407, -84.4277),
    ("KWRB", "Robins AFB", 32.6401, -83.5919),
    ("KCHS", "Joint Base Charleston", 32.8986, -80.0405),
    ("KSSC", "Shaw AFB", 33.9727, -80.4706),
    ("KPOB", "Pope Field / Fort Liberty", 35.1709, -79.0145),
    ("KGSB", "Seymour Johnson AFB", 35.3394, -77.9606),
    ("KNKT", "MCAS Cherry Point", 34.9009, -76.8807),
    ("KNGU", "NS Norfolk", 36.9376, -76.2893),
    ("KLFI", "Langley AFB", 37.0829, -76.3605),
    ("KADW", "Joint Base Andrews", 38.8108, -76.8670),
    ("KNHK", "NAS Patuxent River", 38.2859, -76.4118),
    ("KDOV", "Dover AFB", 39.1295, -75.4660),
    ("KWRI", "Joint Base McGuire-Dix-Lakehurst", 40.0156, -74.5917),
    ("KJFK", "New York JFK", 40.6413, -73.7781),
    ("KFFO", "Wright-Patterson AFB", 39.8261, -84.0483),
    ("KORD", "Chicago O'Hare", 41.9742, -87.9073),
    ("KPSM", "Pease ANGB", 43.0779, -70.8233),
    ("KBGR", "Bangor", 44.8074, -68.8281),
    # Canada, Europe, Middle East & Indo-Pacific
    ("CYQX", "Gander", 48.9369, -54.5681),
    ("CYTR", "CFB Trenton", 44.1189, -77.5281),
    ("CYYZ", "Toronto", 43.6777, -79.6248),
    ("EGVN", "RAF Brize Norton", 51.7500, -1.5836),
    ("EGUL", "RAF Lakenheath", 52.4093, 0.5610),
    ("EGUN", "RAF Mildenhall", 52.3619, 0.4864),
    ("EGXW", "RAF Waddington", 53.1662, -0.5238),
    ("EGLL", "London Heathrow", 51.4700, -0.4543),
    ("LFPG", "Paris CDG", 49.0097, 2.5479),
    ("ETAR", "Ramstein AB", 49.4369, 7.6003),
    ("ETAD", "Spangdahlem AB", 49.9727, 6.6925),
    ("EDDF", "Frankfurt", 50.0379, 8.5622),
    ("EDDP", "Leipzig/Halle", 51.4324, 12.2416),
    ("LERT", "NS Rota", 36.6452, -6.3495),
    ("LICZ", "NAS Sigonella", 37.4017, 14.9224),
    ("LGSA", "NSA Souda Bay", 35.5317, 24.1497),
    ("LTAG", "Incirlik AB", 37.0021, 35.4259),
    ("OTBH", "Al Udeid AB", 25.1173, 51.3150),
    ("OMAM", "Al Dhafra AB", 24.2482, 54.5477),
    ("OMDB", "Dubai", 25.2532, 55.3657),
    ("PHIK", "Joint Base Pearl Harbor-Hickam", 21.3187, -157.9225),
    ("PGUA", "Andersen AFB Guam", 13.5840, 144.9300),
    ("RJTY", "Yokota AB", 35.7485, 139.3485),
    ("RODN", "Kadena AB", 26.3556, 127.7676),
    ("RKSO", "Osan AB", 37.0906, 127.0296),
    ("WSSS", "Singapore Changi", 1.3644, 103.9915),
    ("YSSY", "Sydney", -33.9399, 151.1753),
)

TANKER_TYPES = {"K35R", "K35E", "KC46", "KC10", "KC2", "KC30", "MRTT"}
TANKER_CALLSIGN_PREFIXES = (
    "LAGR", "GOLD", "CLEAN", "PACK", "ETHYL", "SHELL", "TEXACO", "ESSO", "ARCO", "QID", "HOBO", "NCHO", "PEARL", "UPSET",
)

FALLBACK_FLIGHTS = (
    {
        "id": "NASA806",
        "hex": "aaf95a",
        "type": "mixed",
        "origin": "KPMD",
        "destination": "PAFA",
        "from": "Palmdale (KPMD)",
        "to": "Fairbanks (PAFA)",
        "reason": "Airborne Earth-Science & Sensor Laboratory",
        "aboard": "Research Scientists + LIDAR, Spectrometers & Telescopes",
        "anomaly_badge": "AIRBORNE SCIENCE",
        "oddity_score": 93,
        "detail": "Aboard: Research Scientists + LIDAR, Spectrometers & Telescopes. Cruising at 38,000 ft and 452 kt from Palmdale to Fairbanks.",
        "aircraft": "Douglas DC-8-72 (N806NA)",
        "operator": "NASA Airborne Science Program",
        "unit": "NASA Armstrong Flight Research Center",
        "home_base": "Palmdale / Armstrong Bldg 703, CA (KPMD)",
        "registration": "N806NA",
        "altitude": "38,000 ft",
        "vrate_str": "→ LEVEL",
        "speed": "452 kt",
        "speed_kt": 452.0,
        "eta": "ETA 03h 18m",
        "lat": 48.40,
        "lon": -128.20,
        "track": 326.0,
        "origin_coords": [34.6294, -118.0846],
        "dest_coords": [64.8151, -147.8564],
        "has_filed_route": True,
        "is_orbiting": False,
        "orbit_summary": "",
        "orbit_center": None,
        "orbit_radius_m": 0,
        "trail": [[47.90, -127.60], [48.40, -128.20]],
        "sua_id": "",
        "sua_name": "",
        "sua_status": "",
        "aar_partner": "",
        "aar_role": "",
        "aar_partner_coords": None,
        "aar_dist_nm": 0.0,
        "on_demand": False,
        "_alt_ft": 38000,
        "_speed_kt": 452.0,
        "_is_tanker": False,
    },
    {
        "id": "JANET18",
        "hex": "a3ec1f",
        "type": "crew",
        "origin": "KLAS",
        "destination": "KXTA",
        "from": "Las Vegas (KLAS)",
        "to": "Homey / Area 51 (KXTA)",
        "reason": "JANET Classified Test-Range Shuttle",
        "aboard": "Cleared Scientists, Defense Engineers & Range Specialists",
        "anomaly_badge": "INSIDE R-4808N",
        "oddity_score": 98,
        "detail": "Aboard: Cleared Scientists, Defense Engineers & Range Specialists. Climbing through 18,400 ft (+1,650 fpm) out of Las Vegas inside R-4808N.",
        "aircraft": "Boeing 737-600 (N365SR)",
        "operator": "USAF / EG&G Special Projects",
        "unit": "USAF / Department of the Air Force Special Projects",
        "home_base": "Harry Reid Int'l Restricted Terminal, Las Vegas (KLAS)",
        "registration": "N365SR",
        "altitude": "18,400 ft",
        "vrate_str": "↗ +1,650 fpm",
        "speed": "340 kt",
        "speed_kt": 340.0,
        "eta": "ETA 00h 19m",
        "lat": 37.05,
        "lon": -115.72,
        "track": 334.0,
        "origin_coords": [36.0840, -115.1537],
        "dest_coords": [37.2350, -115.8111],
        "has_filed_route": True,
        "is_orbiting": False,
        "orbit_summary": "",
        "orbit_center": None,
        "orbit_radius_m": 0,
        "trail": [[36.45, -115.40], [37.05, -115.72]],
        "sua_id": "R-4808N",
        "sua_name": "Nevada Test & Training Range / Groom Lake (R-4808N)",
        "sua_status": "INSIDE",
        "aar_partner": "",
        "aar_role": "",
        "aar_partner_coords": None,
        "aar_dist_nm": 0.0,
        "on_demand": False,
        "_alt_ft": 18400,
        "_speed_kt": 340.0,
        "_is_tanker": False,
    },
    {
        "id": "ADB3842",
        "hex": "50801d",
        "type": "cargo",
        "origin": "EDDP",
        "destination": "CYQX",
        "from": "Leipzig/Halle (EDDP)",
        "to": "Gander (CYQX)",
        "reason": "Super-Heavy Outsize Cargo Airlift",
        "aboard": "Load Engineers + Satellites, Turbines or Locomotives",
        "anomaly_badge": "OUTSIZE RAMP",
        "oddity_score": 95,
        "detail": "Aboard: Load Engineers + Satellites, Turbines or Locomotives. Cruising at 32,000 ft and 438 kt on an outsize freight leg.",
        "aircraft": "Antonov An-124 Ruslan (UR-82008)",
        "operator": "Antonov Airlines",
        "unit": "Antonov Airlines Heavy Airlift Division",
        "home_base": "Kyiv / Leipzig-Halle Hub (EDDP)",
        "registration": "UR-82008",
        "altitude": "32,000 ft",
        "vrate_str": "→ LEVEL",
        "speed": "438 kt",
        "speed_kt": 438.0,
        "eta": "ETA 02h 44m",
        "lat": 54.80,
        "lon": -24.30,
        "track": 262.0,
        "origin_coords": [51.4324, 12.2416],
        "dest_coords": [48.9369, -54.5681],
        "has_filed_route": True,
        "is_orbiting": False,
        "orbit_summary": "",
        "orbit_center": None,
        "orbit_radius_m": 0,
        "trail": [[54.92, -22.80], [54.80, -24.30]],
        "sua_id": "",
        "sua_name": "",
        "sua_status": "",
        "aar_partner": "",
        "aar_role": "",
        "aar_partner_coords": None,
        "aar_dist_nm": 0.0,
        "on_demand": False,
        "_alt_ft": 32000,
        "_speed_kt": 438.0,
        "_is_tanker": False,
    },
)


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_nm = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return 2.0 * earth_radius_nm * math.asin(min(1.0, math.sqrt(a)))


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def angle_diff_deg(a: float, b: float) -> float:
    return abs(((a - b + 540.0) % 360.0) - 180.0)


def project_geodesic(lat: float, lon: float, bearing_deg: float, distance_nm: float) -> list[float]:
    earth_radius_nm = 3440.065
    angular_dist = distance_nm / earth_radius_nm
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    bearing = math.radians(bearing_deg)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular_dist)
        + math.cos(lat1) * math.sin(angular_dist) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular_dist) * math.cos(lat1),
        math.cos(angular_dist) - math.sin(lat1) * math.sin(lat2),
    )
    norm_lon = ((math.degrees(lon2) + 540.0) % 360.0) - 180.0
    return [round(math.degrees(lat2), 4), round(norm_lon, 4)]


def point_in_polygon(lat: float, lon: float, polygon: list[list[float]]) -> bool:
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        yi, xi = polygon[i][0], polygon[i][1]
        yj, xj = polygon[j][0], polygon[j][1]
        intersect = ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
        )
        if intersect:
            inside = not inside
        j = i
    return inside


def evaluate_special_use_airspace(lat: float, lon: float) -> tuple[str, str, str]:
    """Return (sua_id, sua_name, status) if aircraft is INSIDE or SKIRTING a restricted range."""
    for sua in SPECIAL_USE_AIRSPACES:
        if point_in_polygon(lat, lon, sua["coords"]):
            return sua["id"], sua["name"], "INSIDE"

    closest: tuple[float, dict] | None = None
    for sua in SPECIAL_USE_AIRSPACES:
        coords = sua["coords"]
        min_d = 9999.0
        for idx in range(len(coords)):
            p1 = coords[idx]
            p2 = coords[(idx + 1) % len(coords)]
            for step in range(5):
                frac = step / 4.0
                slat = p1[0] + (p2[0] - p1[0]) * frac
                slon = p1[1] + (p2[1] - p1[1]) * frac
                d = haversine_nm(lat, lon, slat, slon)
                if d < min_d:
                    min_d = d
        if min_d <= 8.0 and (closest is None or min_d < closest[0]):
            closest = (min_d, sua)

    if closest is not None:
        return closest[1]["id"], closest[1]["name"], "SKIRTING"
    return "", "", ""


def infer_origin_base(
    lat: float,
    lon: float,
    track: float,
    alt_ft: int,
    baro_rate: int,
) -> tuple[str, str, float, float] | None:
    """Infer departure airfield by checking reverse-bearing cone on climbs, then nearest base."""
    if baro_rate >= 400 and alt_ft <= 24000:
        back_track = (track + 180.0) % 360.0
        best_dep: tuple[float, tuple[str, str, float, float]] | None = None
        for code, name, blat, blon in STRATEGIC_BASES:
            dist = haversine_nm(lat, lon, blat, blon)
            if 2.0 <= dist <= 130.0:
                brg = initial_bearing_deg(lat, lon, blat, blon)
                err = angle_diff_deg(back_track, brg)
                if err <= 14.0:
                    score = err * 12.0 + dist
                    if best_dep is None or score < best_dep[0]:
                        best_dep = (score, (code, name, blat, blon))
        if best_dep is not None:
            return best_dep[1]

    best: tuple[str, str, float, float] | None = None
    best_dist = 240.0
    for code, name, blat, blon in STRATEGIC_BASES:
        d = haversine_nm(lat, lon, blat, blon)
        if d < best_dist:
            best_dist = d
            best = (code, name, blat, blon)
    return best


def infer_corridor_destination(
    lat: float,
    lon: float,
    track: float,
    alt_ft: int,
    baro_rate: int,
    origin_code: str,
) -> tuple[str, str, float, float, float] | None:
    """Project a narrow ±7.5° great-circle cone forward to infer target military/strategic base."""
    descending = baro_rate <= -450 and alt_ft <= 25000
    min_dist = 4.0 if descending else 14.0
    max_dist = 210.0 if descending else 1350.0
    max_cone_deg = 9.0 if descending else 7.5

    best: tuple[float, tuple[str, str, float, float, float]] | None = None
    for code, name, blat, blon in STRATEGIC_BASES:
        if code == origin_code:
            continue
        dist = haversine_nm(lat, lon, blat, blon)
        if not (min_dist <= dist <= max_dist):
            continue
        brg = initial_bearing_deg(lat, lon, blat, blon)
        err = angle_diff_deg(track, brg)
        if err <= max_cone_deg:
            dist_weight = 18.0 if descending else 34.0
            score = err * 30.0 + (dist / dist_weight)
            if best is None or score < best[0]:
                best = (score, (code, name, blat, blon, dist))

    return best[1] if best is not None else None


def decode_registration_country(registration: str) -> str:
    reg = registration.upper()
    if reg.startswith("N") and len(reg) >= 2 and reg[1].isdigit():
        return "United States"
    for prefix, country in REGISTRATION_PREFIXES:
        if prefix != "N" and reg.startswith(prefix):
            return country
    return ""


def lookup_vip_dossier(raw_hex: str, registration: str, callsign: str) -> dict | None:
    candidates = [
        raw_hex.strip().lower().lstrip("~"),
        registration.strip().upper(),
        re.sub(r"[^A-Z0-9]", "", registration.strip().upper()),
        callsign.strip().upper(),
    ]
    for cand in candidates:
        if cand and cand in VIP_LOOKUP:
            return VIP_LOOKUP[cand]
    return None


def lookup_callsign_profile(callsign: str) -> tuple[str, str, str, str, str, int, str] | None:
    cs = callsign.upper()
    for prefix in sorted(CALLSIGN_PROFILES.keys(), key=len, reverse=True):
        if cs.startswith(prefix) and (len(cs) == len(prefix) or cs[len(prefix):].isalnum()):
            return CALLSIGN_PROFILES[prefix]
    return None


def match_owner_pattern(owner: str) -> tuple[str, str, str, str, int] | None:
    upper_owner = owner.upper()
    if not upper_owner:
        return None
    for keywords, kind, headline_tag, aboard_desc, detail_note, bonus in OWNER_PATTERNS:
        if any(kw in upper_owner for kw in keywords):
            return kind, headline_tag, aboard_desc, detail_note, bonus
    return None


class TrackHistoryStore:
    """Maintains sliding telemetry breadcrumbs per aircraft to detect orbits & loitering."""

    def __init__(self, max_points: int = 16, window_seconds: float = 1500.0) -> None:
        self._lock = threading.Lock()
        self._history: dict[str, deque[tuple[float, float, float, float, int, float]]] = {}
        self.max_points = max_points
        self.window_seconds = window_seconds
        self._writes = 0

    def record_and_analyze(self, raw: dict) -> dict:
        key = (raw.get("hex") or raw["id"]).strip().lower()
        now = time.monotonic()
        lat = float(raw["lat"])
        lon = float(raw["lon"])
        track = float(raw["track"])
        alt_ft = int(raw["alt_ft"])
        speed_kt = float(raw["speed_kt"])
        roll_deg = float(raw.get("roll") or 0.0)
        track_rate = float(raw.get("track_rate") or 0.0)

        with self._lock:
            self._writes += 1
            if self._writes % 100 == 0:
                stale = [
                    k for k, d in self._history.items()
                    if not d or (now - d[-1][0]) > self.window_seconds
                ]
                for k in stale:
                    del self._history[k]

            pts = self._history.get(key)
            if pts is None:
                pts = deque(maxlen=self.max_points)
                self._history[key] = pts

            while pts and (now - pts[0][0]) > self.window_seconds:
                pts.popleft()

            if not pts:
                pts.append((now, lat, lon, track, alt_ft, speed_kt))
            else:
                last_t, last_lat, last_lon, last_hdg, _, _ = pts[-1]
                dist_moved = haversine_nm(last_lat, last_lon, lat, lon)
                hdg_diff = angle_diff_deg(track, last_hdg)
                if (now - last_t) >= 5.0 and (dist_moved >= 0.12 or hdg_diff >= 6.0):
                    pts.append((now, lat, lon, track, alt_ft, speed_kt))

            snapshot = list(pts)

        trail = [[round(p[1], 4), round(p[2], 4)] for p in snapshot]
        is_orbiting = False
        orbit_summary = ""
        orbit_center: list[float] | None = None
        orbit_radius_m = 0

        if len(snapshot) >= 3:
            deltas: list[float] = []
            max_span_nm = 0.0
            for i in range(1, len(snapshot)):
                h1 = snapshot[i - 1][3]
                h2 = snapshot[i][3]
                d_hdg = ((h2 - h1 + 540.0) % 360.0) - 180.0
                if abs(d_hdg) <= 135.0:
                    deltas.append(d_hdg)
            for i in range(len(snapshot)):
                for j in range(i + 1, len(snapshot)):
                    span = haversine_nm(snapshot[i][1], snapshot[i][2], snapshot[j][1], snapshot[j][2])
                    if span > max_span_nm:
                        max_span_nm = span

            net_turn = sum(deltas)
            total_turn = sum(abs(d) for d in deltas)
            if (abs(net_turn) >= 210.0 or total_turn >= 280.0) and max_span_nm <= 35.0:
                is_orbiting = True
                c_lat = sum(p[1] for p in snapshot) / len(snapshot)
                c_lon = sum(p[2] for p in snapshot) / len(snapshot)
                radii = [haversine_nm(c_lat, c_lon, p[1], p[2]) for p in snapshot]
                mean_r_nm = max(1.5, min(20.0, sum(radii) / len(radii)))
                direction = "clockwise" if net_turn >= 0 else "counter-clockwise"
                orbit_center = [round(c_lat, 4), round(c_lon, 4)]
                orbit_radius_m = int(round(mean_r_nm * 1852.0))
                orbit_summary = f"Confirmed {direction} orbit/loiter ({int(round(abs(net_turn)))}° turn inside {max_span_nm:.1f} nm)."

        if not is_orbiting and speed_kt >= 70.0 and alt_ft >= 1500:
            effective_bank = 0.0
            if abs(roll_deg) >= 17.0:
                effective_bank = roll_deg
            elif abs(track_rate) >= 1.8:
                omega_rad = math.radians(track_rate)
                v_fps = speed_kt * 1.68781
                tan_phi = (v_fps * omega_rad) / 32.174
                effective_bank = math.degrees(math.atan(tan_phi))

            if abs(effective_bank) >= 17.0:
                v_fps = speed_kt * 1.68781
                r_ft = (v_fps ** 2) / (32.174 * math.tan(math.radians(min(60.0, abs(effective_bank)))))
                r_nm = max(1.2, min(18.0, r_ft / 6076.12))
                side_bearing = (track + (90.0 if effective_bank > 0 else -90.0)) % 360.0
                orbit_center = project_geodesic(lat, lon, side_bearing, r_nm)
                orbit_radius_m = int(round(r_nm * 1852.0))
                is_orbiting = True
                direction = "right-hand" if effective_bank > 0 else "left-hand"
                orbit_summary = f"Sustained {direction} banking orbit ({abs(effective_bank):.0f}° bank, ~{r_nm:.1f} nm turn radius)."

        return {
            "trail": trail,
            "is_orbiting": is_orbiting,
            "orbit_summary": orbit_summary,
            "orbit_center": orbit_center,
            "orbit_radius_m": orbit_radius_m,
        }


TRACK_HISTORY = TrackHistoryStore()


def fetch_json(url: str, timeout: float = 5.0, method: str = "GET", body: bytes | None = None) -> object:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = Request(url, data=body, headers=headers, method=method)
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


HEX_AIRCRAFT_CACHE: dict[str, dict] = {}
HEX_CACHE_LOCK = threading.Lock()

AIRPORT_CACHE: dict[str, dict] = {}
AIRPORT_CACHE_LOCK = threading.Lock()

ROUTE_CACHE: dict[str, tuple[float, dict | None]] = {}
ROUTE_CACHE_LOCK = threading.Lock()


def lookup_hex_aircraft(icao_hex: str) -> dict:
    hex_key = icao_hex.strip().lower().lstrip("~")
    if not hex_key or len(hex_key) != 6:
        return {}
    with HEX_CACHE_LOCK:
        if hex_key in HEX_AIRCRAFT_CACHE:
            return HEX_AIRCRAFT_CACHE[hex_key]
    info: dict = {}
    try:
        data = fetch_json(f"https://hexdb.io/api/v1/aircraft/{hex_key}", timeout=2.5)
        if isinstance(data, dict) and data.get("ModeS"):
            info = data
    except Exception:
        info = {}
    with HEX_CACHE_LOCK:
        HEX_AIRCRAFT_CACHE[hex_key] = info
    return info


def lookup_airport_icao(icao: str) -> dict | None:
    code = icao.strip().upper()
    if len(code) != 4:
        return None
    with AIRPORT_CACHE_LOCK:
        if code in AIRPORT_CACHE:
            return AIRPORT_CACHE[code] or None
    info: dict = {}
    try:
        data = fetch_json(f"https://hexdb.io/api/v1/airport/icao/{code}", timeout=2.5)
        if isinstance(data, dict) and data.get("latitude") is not None:
            info = {
                "icao": code,
                "iata": (data.get("iata") or code).strip().upper(),
                "city": (data.get("region_name") or data.get("airport") or code).strip(),
                "lat": float(data["latitude"]),
                "lon": float(data["longitude"]),
            }
    except Exception:
        info = {}
    with AIRPORT_CACHE_LOCK:
        AIRPORT_CACHE[code] = info
    return info or None


def resolve_routes_batch(aircraft_list: list[dict]) -> dict[str, dict]:
    now = time.monotonic()
    resolved: dict[str, dict] = {}
    uncached: list[dict] = []

    with ROUTE_CACHE_LOCK:
        for ac in aircraft_list:
            cs = ac["id"]
            cached = ROUTE_CACHE.get(cs)
            if cached and (now - cached[0]) < ROUTE_TTL_SECONDS:
                if cached[1]:
                    resolved[cs] = cached[1]
            else:
                uncached.append(ac)

    if not uncached:
        return resolved

    payload = json.dumps({
        "planes": [{"callsign": ac["id"], "lat": ac["lat"], "lng": ac["lon"]} for ac in uncached]
    }).encode("utf-8")
    try:
        resp = fetch_json("https://api.adsb.lol/api/0/routeset", timeout=4.5, method="POST", body=payload)
        if isinstance(resp, list):
            for entry in resp:
                if not isinstance(entry, dict):
                    continue
                cs = str(entry.get("callsign") or "").strip().upper()
                airports = entry.get("_airports") or []
                if cs and len(airports) >= 2:
                    orig, dest = airports[0], airports[-1]
                    if orig.get("lat") is not None and dest.get("lat") is not None:
                        resolved[cs] = {
                            "origin_code": (orig.get("iata") or orig.get("icao") or "ORIG").upper(),
                            "origin_city": (orig.get("location") or orig.get("name") or orig.get("icao") or "Origin").strip(),
                            "origin_coords": [round(float(orig["lat"]), 4), round(float(orig["lon"]), 4)],
                            "dest_code": (dest.get("iata") or dest.get("icao") or "DEST").upper(),
                            "dest_city": (dest.get("location") or dest.get("name") or dest.get("icao") or "Destination").strip(),
                            "dest_coords": [round(float(dest["lat"]), 4), round(float(dest["lon"]), 4)],
                        }
    except Exception as exc:
        logging.debug("adsb.lol routeset lookup skipped: %s", exc)

    missing = [ac["id"] for ac in uncached if ac["id"] not in resolved]

    def fetch_hexdb_route(cs: str) -> tuple[str, dict | None]:
        try:
            data = fetch_json(f"https://hexdb.io/api/v1/route/icao/{quote(cs)}", timeout=2.5)
            if isinstance(data, dict) and "-" in str(data.get("route", "")):
                parts = [p.strip().upper() for p in str(data["route"]).split("-") if len(p.strip()) == 4]
                if len(parts) >= 2:
                    orig = lookup_airport_icao(parts[0])
                    dest = lookup_airport_icao(parts[-1])
                    if orig and dest:
                        return cs, {
                            "origin_code": orig["iata"] or orig["icao"],
                            "origin_city": orig["city"],
                            "origin_coords": [round(orig["lat"], 4), round(orig["lon"], 4)],
                            "dest_code": dest["iata"] or dest["icao"],
                            "dest_city": dest["city"],
                            "dest_coords": [round(dest["lat"], 4), round(dest["lon"], 4)],
                        }
        except Exception:
            pass
        return cs, None

    if missing:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for cs, route_obj in pool.map(fetch_hexdb_route, missing[:24]):
                if route_obj:
                    resolved[cs] = route_obj

    with ROUTE_CACHE_LOCK:
        for ac in uncached:
            cs = ac["id"]
            ROUTE_CACHE[cs] = (now, resolved.get(cs))

    return resolved


def format_eta_from_nm(rem_nm: float, speed_kt: float, prefix: str = "ETA ") -> str:
    if speed_kt <= 60.0:
        return f"{int(round(rem_nm))} nm rem"
    hours = rem_nm / speed_kt
    hrs = int(hours)
    mins = int(round((hours - hrs) * 60))
    if mins == 60:
        hrs += 1
        mins = 0
    return f"{prefix}{hrs:02d}h {mins:02d}m"


def build_flight_record(raw: dict, hex_info: dict, route_info: dict | None) -> dict:
    callsign = raw["id"]
    raw_hex = raw["hex"].lstrip("~")
    registration = (hex_info.get("Registration") or raw["registration"] or raw_hex).strip().upper()
    type_code = (hex_info.get("ICAOTypeCode") or raw["type_code"]).strip().upper()
    hex_owner = (hex_info.get("RegisteredOwners") or "").strip()
    hex_type_name = (hex_info.get("Type") or "").strip()

    raw_operator = raw["raw_operator"] or hex_owner
    ac_info = AIRCRAFT_CATALOG.get(type_code)
    vip_entry = lookup_vip_dossier(raw_hex, registration, callsign)
    cs_profile = lookup_callsign_profile(callsign)
    owner_match = match_owner_pattern(raw_operator)
    country = decode_registration_country(registration)

    aircraft_model = (
        ac_info[0]
        if ac_info
        else (hex_type_name or raw["raw_desc"] or type_code or "Tracked Airframe")
    )
    operator = raw_operator or (cs_profile[0] if cs_profile else (f"{country} Registry" if country else "Public ADS-B"))

    lat, lon = raw["lat"], raw["lon"]
    alt_ft = raw["alt_ft"]
    speed_kt = raw["speed_kt"]
    track = raw["track"]
    baro_rate = raw["baro_rate"]
    squawk = raw["squawk"]
    is_pia = bool(raw.get("is_pia")) or raw["hex"].startswith("~")
    is_on_demand = bool(raw.get("on_demand"))

    orbit_info = TRACK_HISTORY.record_and_analyze(raw)
    sua_id, sua_name, sua_status = evaluate_special_use_airspace(lat, lon)

    unit_name = ""
    home_base = ""

    if vip_entry:
        kind = vip_entry["type"]
        headline = vip_entry["title"]
        aboard = vip_entry["aboard"]
        mission_note = vip_entry["mission"]
        oddity = vip_entry["oddity"]
        badge = vip_entry["badge"]
        unit_name = vip_entry["unit"]
        home_base = vip_entry["home_base"]
        if not raw_operator:
            operator = vip_entry["unit"]
    elif cs_profile:
        kind = cs_profile[1]
        headline = f"{cs_profile[2]} ({aircraft_model})"
        aboard = cs_profile[3]
        mission_note = cs_profile[4]
        oddity = cs_profile[5]
        badge = cs_profile[6]
    elif owner_match:
        kind, headline_tag, aboard, mission_note, bonus = owner_match
        headline = f"{headline_tag} — {aircraft_model}"
        base_odd = ac_info[4] if ac_info else 64
        oddity = min(98, base_odd + bonus)
        badge = headline_tag.split("(")[0].strip().upper()[:22]
    elif ac_info:
        kind = ac_info[1]
        aboard = ac_info[2]
        mission_note = ac_info[3]
        oddity = ac_info[4]
        if is_pia:
            headline = f"Anonymous PIA Stealth-Hex Flight — {aircraft_model}"
            oddity = max(oddity, 82)
            badge = "PIA STEALTH HEX"
        elif raw["db_flags"] & 1:
            headline = f"Military / Government Sortie — {aircraft_model}"
            badge = "GOV / MIL OPS"
        elif raw["db_flags"] & 8:
            headline = f"{operator} · {aircraft_model}" if raw_operator else f"LADD-Shielded {aircraft_model}"
            badge = "LADD SHIELDED"
        else:
            headline = f"{operator} · {aircraft_model}"
            badge = "ON-DEMAND TARGET" if is_on_demand else "SPECIAL AIRFRAME"
    else:
        kind = "crew"
        aboard = (
            "Commercial Flight Crew + Scheduled Airline Passengers"
            if is_on_demand and route_info
            else "Flight Crew + Specialist / Executive Passengers"
        )
        mission_note = "Live Mode-S/ADS-B state vector enriched with public aircraft registry metadata."
        oddity = 79 if is_pia else (50 if is_on_demand else 65)
        headline = f"{'PIA Stealth-Hex' if is_pia else operator} · {aircraft_model}"
        badge = "PIA STEALTH HEX" if is_pia else ("ON-DEMAND TARGET" if is_on_demand else "SPECIAL OPS")

    if is_pia and not vip_entry:
        oddity = min(86, oddity + 4)
        badge = "PIA STEALTH HEX"
    if alt_ft >= 45000:
        oddity = min(99, oddity + 7)
        if badge in {"SPECIAL AIRFRAME", "LADD SHIELDED", "ON-DEMAND TARGET"}:
            badge = f"STRATOSPHERE FL{alt_ft // 100}"
    elif 1200 <= alt_ft <= 7500 and 95 <= speed_kt <= 175 and type_code in {"B350", "BE20", "PC12", "C208", "DHC6"}:
        oddity = min(99, oddity + 11)
        aboard = "Sensor Operators + Low-Altitude LIDAR / Geophysical Survey Array"
        badge = "LOW-ALT SURVEY"

    if sua_status == "INSIDE":
        oddity = min(99, oddity + 12)
        if not vip_entry and badge not in {"ORBIT / LOITER", "SQUAWK 7700"}:
            badge = f"INSIDE {sua_id}"
    elif sua_status == "SKIRTING":
        oddity = min(99, oddity + 6)
        if not vip_entry and badge in {"SPECIAL AIRFRAME", "LADD SHIELDED", "GOV / MIL OPS", "SPECIAL OPS"}:
            badge = f"NEAR {sua_id}"

    if orbit_info["is_orbiting"]:
        oddity = min(99, oddity + 13)
        if not vip_entry:
            badge = "ORBIT / LOITER"

    if squawk in {"7700", "7600", "7500"}:
        oddity = 99
        badge = f"SQUAWK {squawk}"

    # Route or Bearing-Cone Corridor Inference
    if route_info:
        origin_code = route_info["origin_code"]
        dest_code = route_info["dest_code"]
        from_label = f"{route_info['origin_city']} ({origin_code})"
        to_label = f"{route_info['dest_city']} ({dest_code})"
        origin_coords = route_info["origin_coords"]
        dest_coords = route_info["dest_coords"]
        rem_nm = haversine_nm(lat, lon, dest_coords[0], dest_coords[1])
        eta_label = format_eta_from_nm(rem_nm, speed_kt, prefix="ETA ")
        route_sentence = f"Flying filed route from {from_label} to {to_label} ({eta_label})."
        has_corridor = True
    else:
        near_orig = infer_origin_base(lat, lon, track, alt_ft, baro_rate)
        if near_orig:
            origin_code = near_orig[0]
            from_label = f"Near {near_orig[1]} ({near_orig[0]})"
            origin_coords = [round(near_orig[2], 4), round(near_orig[3], 4)]
        else:
            origin_code = registration
            from_label = f"{lat:.2f}°, {lon:.2f}°" + (f" ({country})" if country else "")
            origin_coords = project_geodesic(lat, lon, (track + 180.0) % 360.0, max(20.0, speed_kt * 0.35))

        corridor = infer_corridor_destination(lat, lon, track, alt_ft, baro_rate, origin_code)
        if corridor:
            c_code, c_name, c_lat, c_lon, c_dist = corridor
            dest_code = f"~{c_code}"
            to_label = f"Est. {c_name} ({c_code} · {int(round(c_dist))} nm)"
            dest_coords = [round(c_lat, 4), round(c_lon, 4)]
            eta_label = format_eta_from_nm(c_dist, speed_kt, prefix="~")
            route_sentence = (
                f"Departed {from_label}; bearing cone ({int(round(track)):03d}°) aligns with "
                f"{c_name} ({c_code}, {int(round(c_dist))} nm ahead)."
            )
            has_corridor = True
        else:
            dest_code = f"HDG {int(round(track)):03d}°"
            to_label = f"Heading {int(round(track)):03d}° · {country or 'Open Airspace'}"
            dest_coords = project_geodesic(lat, lon, track, max(50.0, speed_kt * 0.75))
            eta_label = f"HDG {int(round(track)):03d}°"
            route_sentence = f"Operating out of {from_label} on heading {int(round(track)):03d}° (open-airspace vector)."
            has_corridor = False

    if abs(baro_rate) >= 350:
        vrate_str = f"{'↗' if baro_rate > 0 else '↘'} {baro_rate:+,} fpm"
        phase = f"{'climbing' if baro_rate > 0 else 'descending'} through {alt_ft:,} ft ({baro_rate:+,} fpm) at {int(round(speed_kt))} kt"
    else:
        vrate_str = "→ LEVEL"
        phase = f"cruising at {alt_ft:,} ft and {int(round(speed_kt))} kt"

    detail_parts = [
        f"Aboard: {aboard}.",
        f"Currently {phase}.",
        route_sentence,
    ]
    if sua_status:
        detail_parts.append(f"AIRSPACE: {sua_status} {sua_name} [SUA].")
    if orbit_info["is_orbiting"]:
        detail_parts.append(orbit_info["orbit_summary"])
    detail_parts.append(mission_note)

    if is_pia:
        detail_parts.append("Flying under a rolling FAA Privacy ICAO Address (PIA) anonymous hex code.")
    if squawk == "7700":
        detail_parts.append("ALERT: Broadcasting Emergency Squawk 7700.")
    elif squawk == "7600":
        detail_parts.append("NOTICE: Broadcasting Radio Failure Squawk 7600.")

    is_tanker = (
        type_code in TANKER_TYPES
        or any(callsign.startswith(p) for p in TANKER_CALLSIGN_PREFIXES)
        or ((raw["db_flags"] & 1) and type_code in {"A332", "A333", "C130", "C30J", "A400"} and "TANKER" in headline.upper())
    )

    return {
        "id": callsign,
        "hex": raw_hex,
        "type": kind,
        "origin": origin_code,
        "destination": dest_code,
        "from": from_label,
        "to": to_label,
        "reason": headline,
        "aboard": aboard,
        "anomaly_badge": badge,
        "oddity_score": oddity,
        "detail": " ".join(detail_parts),
        "aircraft": f"{aircraft_model} ({registration})",
        "operator": operator,
        "unit": unit_name or operator,
        "home_base": home_base or from_label,
        "registration": registration,
        "altitude": f"{alt_ft:,} ft",
        "vrate_str": vrate_str,
        "speed": f"{int(round(speed_kt))} kt",
        "speed_kt": round(speed_kt, 1),
        "eta": eta_label,
        "lat": round(lat, 4),
        "lon": round(lon, 4),
        "track": round(track, 1),
        "origin_coords": origin_coords,
        "dest_coords": dest_coords,
        "has_filed_route": bool(route_info) or has_corridor,
        "is_orbiting": orbit_info["is_orbiting"],
        "orbit_summary": orbit_info["orbit_summary"],
        "orbit_center": orbit_info["orbit_center"],
        "orbit_radius_m": orbit_info["orbit_radius_m"],
        "trail": orbit_info["trail"],
        "sua_id": sua_id,
        "sua_name": sua_name,
        "sua_status": sua_status,
        "aar_partner": "",
        "aar_role": "",
        "aar_partner_coords": None,
        "aar_dist_nm": 0.0,
        "on_demand": is_on_demand,
        "_alt_ft": alt_ft,
        "_speed_kt": speed_kt,
        "_is_tanker": is_tanker,
    }


def detect_aerial_refueling_pairs(flights: list[dict]) -> None:
    """Pair airborne tankers with nearby receivers matching altitude, heading, and speed."""
    tankers = [f for f in flights if f.get("_is_tanker") and f.get("_alt_ft", 0) >= 10000]
    if not tankers:
        return

    used_receivers: set[str] = set()
    for tanker in tankers:
        best_match: tuple[float, dict] | None = None
        for cand in flights:
            if cand["id"] == tanker["id"] or cand["id"] in used_receivers:
                continue
            if cand.get("_alt_ft", 0) < 10000:
                continue
            alt_diff = abs(tanker["_alt_ft"] - cand["_alt_ft"])
            if alt_diff > 1000:
                continue
            hdg_diff = angle_diff_deg(tanker["track"], cand["track"])
            if hdg_diff > 15.0:
                continue
            spd_diff = abs(tanker["_speed_kt"] - cand["_speed_kt"])
            if spd_diff > 35.0:
                continue
            dist_nm = haversine_nm(tanker["lat"], tanker["lon"], cand["lat"], cand["lon"])
            if dist_nm <= 3.5:
                if best_match is None or dist_nm < best_match[0]:
                    best_match = (dist_nm, cand)

        if best_match is not None:
            dist_nm, receiver = best_match
            used_receivers.add(receiver["id"])

            tanker["aar_partner"] = receiver["id"]
            tanker["aar_role"] = "TANKER"
            tanker["aar_partner_coords"] = [receiver["lat"], receiver["lon"]]
            tanker["aar_dist_nm"] = round(dist_nm, 2)
            tanker["oddity_score"] = min(99, tanker["oddity_score"] + 12)
            tanker["anomaly_badge"] = f"AAR · {receiver['id']}"
            tanker["detail"] += (
                f" AERIAL REFUELING ACTIVE: Married with receiver {receiver['id']} "
                f"({dist_nm:.1f} nm separation at FL{tanker['_alt_ft'] // 100})."
            )

            receiver["aar_partner"] = tanker["id"]
            receiver["aar_role"] = "RECEIVER"
            receiver["aar_partner_coords"] = [tanker["lat"], tanker["lon"]]
            receiver["aar_dist_nm"] = round(dist_nm, 2)
            receiver["oddity_score"] = min(99, receiver["oddity_score"] + 12)
            receiver["anomaly_badge"] = f"AAR · {tanker['id']}"
            receiver["detail"] += (
                f" AERIAL REFUELING ACTIVE: Refueling from tanker {tanker['id']} "
                f"({dist_nm:.1f} nm separation at FL{receiver['_alt_ft'] // 100})."
            )


def strip_internal_fields(flights: list[dict]) -> list[dict]:
    cleaned: list[dict] = []
    for f in flights:
        cleaned.append({k: v for k, v in f.items() if not k.startswith("_")})
    return cleaned


def parse_adsb_lol_item(
    ac: dict,
    default_flag: int = 0,
    is_pia: bool = False,
    on_demand: bool = False,
    allow_low_alt: bool = False,
) -> dict | None:
    lat = ac.get("lat")
    lon = ac.get("lon")
    alt = ac.get("alt_baro")
    if lat is None or lon is None or alt in (None, "ground"):
        return None
    try:
        alt_ft = int(float(alt))
    except (TypeError, ValueError):
        return None
    if not allow_low_alt and alt_ft < 1000:
        return None

    raw_hex = str(ac.get("hex") or "").strip().lower()
    raw_callsign = (ac.get("flight") or ac.get("r") or raw_hex.lstrip("~") or "").strip().upper()
    callsign = re.sub(r"[^A-Z0-9._-]", "", raw_callsign)
    if len(callsign) < 2:
        return None

    return {
        "id": callsign,
        "hex": raw_hex,
        "registration": str(ac.get("r") or raw_hex.lstrip("~") or "N/A").strip().upper(),
        "type_code": str(ac.get("t") or "").strip().upper(),
        "raw_desc": str(ac.get("desc") or "").strip(),
        "raw_operator": str(ac.get("ownOp") or "").strip(),
        "lat": float(lat),
        "lon": float(lon),
        "alt_ft": max(0, alt_ft),
        "speed_kt": float(ac.get("gs") or 0.0),
        "track": float(ac.get("track") or 0.0),
        "baro_rate": int(float(ac.get("baro_rate") or 0)),
        "roll": float(ac.get("roll") or 0.0),
        "track_rate": float(ac.get("track_rate") or 0.0),
        "squawk": str(ac.get("squawk") or "----").strip(),
        "db_flags": int(ac.get("dbFlags") or default_flag),
        "is_pia": is_pia or raw_hex.startswith("~"),
        "on_demand": on_demand,
    }


def lookup_on_demand_targets(tokens: list[str], on_demand: bool = True) -> list[dict]:
    """Query live ADS-B by exact callsign, registration, or 6-char ICAO hex."""
    clean_tokens: list[str] = []
    for tok in tokens:
        cleaned = re.sub(r"[^A-Z0-9-]", "", tok.strip().upper())
        if 2 <= len(cleaned) <= 12 and cleaned not in clean_tokens:
            clean_tokens.append(cleaned)
    if not clean_tokens:
        return []

    urls: list[str] = []
    for tok in clean_tokens[:6]:
        urls.append(f"https://api.adsb.lol/v2/callsign/{quote(tok)}")
        urls.append(f"https://api.adsb.lol/v2/reg/{quote(tok)}")
        if re.fullmatch(r"[0-9A-F]{6}", tok):
            urls.append(f"https://api.adsb.lol/v2/hex/{quote(tok.lower())}")

    def query_url(url: str) -> list[dict]:
        found: list[dict] = []
        try:
            data = fetch_json(url, timeout=3.8)
            if isinstance(data, dict):
                for ac in data.get("ac", []):
                    parsed = parse_adsb_lol_item(ac, on_demand=on_demand, allow_low_alt=True)
                    if parsed:
                        found.append(parsed)
        except Exception:
            pass
        return found

    raw_found: list[dict] = []
    seen_ids: set[str] = set()
    with ThreadPoolExecutor(max_workers=8) as pool:
        for batch in pool.map(query_url, urls):
            for item in batch:
                if item["id"] not in seen_ids:
                    seen_ids.add(item["id"])
                    raw_found.append(item)

    if not raw_found:
        return []

    hex_map: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        future_hex = {
            pool.submit(lookup_hex_aircraft, item["hex"]): item["hex"]
            for item in raw_found
            if item["hex"] and not item["hex"].startswith("~")
        }
        route_map = resolve_routes_batch(raw_found)
        for fut, hex_code in future_hex.items():
            try:
                hex_map[hex_code] = fut.result(timeout=2.5)
            except Exception:
                hex_map[hex_code] = {}

    return [
        build_flight_record(
            raw=item,
            hex_info=hex_map.get(item["hex"], {}),
            route_info=route_map.get(item["id"]),
        )
        for item in raw_found
    ]


def fetch_opensky_specials(limit: int = 10) -> list[dict]:
    special_prefixes = (
        "NASA", "NOAA", "TEAL", "ADB", "VDA", "BGA", "JANET", "KBA",
        "CUL", "TNKR", "USCG", "LIFE", "MED", "GTI", "CKS",
    )
    results: list[dict] = []
    try:
        data = fetch_json("https://opensky-network.org/api/states/all", timeout=5.5)
        if not isinstance(data, dict):
            return []
        for state in data.get("states") or []:
            if len(state) < 15 or state[8]:
                continue
            raw_cs = (state[1] or "").strip().upper()
            callsign = re.sub(r"[^A-Z0-9._-]", "", raw_cs)
            if not callsign.startswith(special_prefixes):
                continue
            lon, lat, baro_alt, vel_ms, track = state[5], state[6], state[7], state[9], state[10]
            if lon is None or lat is None or baro_alt is None or baro_alt < 300:
                continue
            results.append({
                "id": callsign,
                "hex": str(state[0] or "").strip().lower(),
                "registration": str(state[0] or "HEX").strip().upper(),
                "type_code": "",
                "raw_desc": "",
                "raw_operator": str(state[2] or "").strip(),
                "lat": float(lat),
                "lon": float(lon),
                "alt_ft": int(round(float(baro_alt) * 3.28084)),
                "speed_kt": float(vel_ms or 0.0) * 1.94384,
                "track": float(track or 0.0),
                "baro_rate": int(round(float(state[11] or 0.0) * 196.85)),
                "roll": 0.0,
                "track_rate": 0.0,
                "squawk": str(state[14] or "----").strip(),
                "db_flags": 0,
                "is_pia": False,
                "on_demand": False,
            })
            if len(results) >= limit:
                break
    except Exception as exc:
        logging.debug("OpenSky special-mission scan skipped: %s", exc)
    return results


def fetch_live_flights(limit: int = 38) -> tuple[list[dict], str]:
    sources: list[tuple[str, int, bool, int]] = [
        ("https://api.adsb.lol/v2/type/E6", 1, False, 3),
        ("https://api.adsb.lol/v2/type/R135", 1, False, 3),
        ("https://api.adsb.lol/v2/type/A124", 0, False, 4),
        ("https://api.adsb.lol/v2/type/A337", 0, False, 3),
        ("https://api.adsb.lol/v2/type/P3", 1, False, 3),
        ("https://api.adsb.lol/v2/type/C5M", 1, False, 3),
        ("https://api.adsb.lol/v2/type/K35R", 1, False, 5),
        ("https://api.adsb.lol/v2/type/KC46", 1, False, 4),
        ("https://api.adsb.lol/v2/mil", 1, False, 14),
        ("https://api.adsb.lol/v2/ladd", 8, False, 8),
        ("https://api.adsb.lol/v2/pia", 0, True, 3),
    ]

    def pull_endpoint(spec: tuple[str, int, bool, int]) -> list[dict]:
        url, default_flag, is_pia, cap = spec
        items: list[dict] = []
        try:
            data = fetch_json(url, timeout=4.5)
            if isinstance(data, dict):
                for ac in data.get("ac", []):
                    parsed = parse_adsb_lol_item(ac, default_flag=default_flag, is_pia=is_pia)
                    if parsed:
                        items.append(parsed)
                        if len(items) >= cap:
                            break
        except Exception as exc:
            logging.debug("Endpoint %s skipped: %s", url, exc)
        return items

    buckets: list[list[dict]] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(pull_endpoint, s) for s in sources]
        opensky_fut = pool.submit(fetch_opensky_specials, 8)
        for fut in futures:
            buckets.append(fut.result())
        buckets.append(opensky_fut.result())

    seen_ids: set[str] = set()
    raw_items: list[dict] = []
    max_len = max((len(b) for b in buckets), default=0)
    for idx in range(max_len):
        for bucket in buckets:
            if idx < len(bucket):
                item = bucket[idx]
                if item["id"] not in seen_ids:
                    seen_ids.add(item["id"])
                    raw_items.append(item)
                    if len(raw_items) >= limit:
                        break
        if len(raw_items) >= limit:
            break

    if not raw_items:
        return [], ""

    hex_map: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        future_hex = {
            pool.submit(lookup_hex_aircraft, item["hex"]): item["hex"]
            for item in raw_items
            if item["hex"] and not item["hex"].startswith("~")
        }
        route_map = resolve_routes_batch(raw_items)
        for fut, hex_code in future_hex.items():
            try:
                hex_map[hex_code] = fut.result(timeout=2.8)
            except Exception:
                hex_map[hex_code] = {}

    enriched = [
        build_flight_record(
            raw=item,
            hex_info=hex_map.get(item["hex"], {}),
            route_info=route_map.get(item["id"]),
        )
        for item in raw_items
    ]
    detect_aerial_refueling_pairs(enriched)
    enriched.sort(key=lambda f: f["oddity_score"], reverse=True)
    return strip_internal_fields(enriched), "LIVE · OSINT RADAR"


class FlightFeedCache:
    """Thread-safe TTL cache with persistent on-demand & watched aircraft tracking."""

    def __init__(self, ttl: int = CACHE_TTL_SECONDS) -> None:
        self.ttl = ttl
        self._lock = threading.Lock()
        self._flights: list[dict] = strip_internal_fields(list(FALLBACK_FLIGHTS))
        self._source = "OFFLINE SAMPLE"
        self._last_fetch = 0.0
        self._pinned: dict[str, tuple[float, dict]] = {}

    def _merge_pinned(self, base_flights: list[dict]) -> list[dict]:
        now = time.monotonic()
        with self._lock:
            expired = [k for k, (exp, _) in self._pinned.items() if exp <= now]
            for k in expired:
                del self._pinned[k]
            by_id = {f["id"]: f for f in base_flights}
            pinned_list: list[dict] = []
            for fid, (_, fobj) in self._pinned.items():
                if fid in by_id:
                    pinned_list.append(by_id.pop(fid))
                else:
                    pinned_list.append(fobj)
            return pinned_list + list(by_id.values())

    def search_on_demand(self, query: str) -> list[dict]:
        found = lookup_on_demand_targets([query], on_demand=True)
        if not found:
            return []
        cleaned = strip_internal_fields(found)
        exp = time.monotonic() + ON_DEMAND_TTL_SECONDS
        with self._lock:
            for item in cleaned:
                self._pinned[item["id"]] = (exp, item)
        return cleaned

    def get_flights(
        self,
        force_refresh: bool = False,
        extra_callsigns: set[str] | None = None,
    ) -> tuple[list[dict], str]:
        now = time.monotonic()
        with self._lock:
            need_fetch = force_refresh or (now - self._last_fetch) >= self.ttl or self._last_fetch == 0.0
            cached_base = list(self._flights)
            cached_source = self._source
            pinned_ids = set(self._pinned.keys())

        if not need_fetch:
            return self._merge_pinned(cached_base), cached_source

        flights, source = fetch_live_flights(limit=38)
        base_list = flights if flights else cached_base
        final_source = source if flights else cached_source

        present_ids = {f["id"] for f in base_list}
        missing_tracked = list((pinned_ids | (extra_callsigns or set())) - present_ids)
        if missing_tracked:
            refreshed_extras = strip_internal_fields(
                lookup_on_demand_targets(missing_tracked[:6], on_demand=True)
            )
            exp = time.monotonic() + ON_DEMAND_TTL_SECONDS
            with self._lock:
                for item in refreshed_extras:
                    if item["id"] in self._pinned:
                        self._pinned[item["id"]] = (exp, item)
                extra_by_id = {item["id"]: item for item in refreshed_extras}
            base_list = base_list + [f for fid, f in extra_by_id.items() if fid not in present_ids]

        with self._lock:
            if flights:
                self._flights = flights
                self._source = final_source
            self._last_fetch = time.monotonic()

        return self._merge_pinned(base_list), final_source


FEED_CACHE = FlightFeedCache()


def filter_flights(flights: list[dict], category: str, query: str) -> list[dict]:
    needle = query.strip().casefold()
    skip_keys = {
        "origin_coords", "dest_coords", "has_filed_route",
        "orbit_center", "trail", "aar_partner_coords", "on_demand",
    }
    return [
        flight for flight in flights
        if (category == "all" or flight["type"] == category or flight["type"] == "mixed")
        and (
            not needle
            or needle in " ".join(
                str(v) for k, v in flight.items() if k not in skip_keys
            ).casefold()
        )
    ]


def should_trigger_on_demand(query: str, local_matches: list[dict]) -> bool:
    cleaned = query.strip().upper()
    if not cleaned or " " in cleaned:
        return False
    if not re.fullmatch(r"[A-Z0-9-]{2,12}", cleaned):
        return False
    if not local_matches:
        return True
    exact_ids = (
        {f["id"].upper() for f in local_matches}
        | {f["registration"].upper() for f in local_matches}
        | {f["hex"].upper() for f in local_matches}
    )
    return cleaned not in exact_ids and bool(re.search(r"\d", cleaned))


def resolve_dashboard_state(
    category: str,
    query: str,
    selected_id: str,
    watched: set[str],
    force_refresh: bool = False,
) -> dict:
    if category not in VALID_FILTERS:
        category = "all"
    query = query[:100]

    all_flights, feed_source = FEED_CACHE.get_flights(
        force_refresh=force_refresh,
        extra_callsigns=watched,
    )
    shown = filter_flights(all_flights, category, query)

    if should_trigger_on_demand(query, shown):
        on_demand_hits = FEED_CACHE.search_on_demand(query)
        if on_demand_hits:
            all_flights, feed_source = FEED_CACHE.get_flights(force_refresh=False)
            shown = filter_flights(all_flights, category, query)
            if not shown and category != "all":
                category = "all"
                shown = filter_flights(all_flights, category, query)
            if shown and not selected_id:
                selected_id = on_demand_hits[0]["id"]

    flight_by_id = {flight["id"]: flight for flight in all_flights}
    if selected_id not in {flight["id"] for flight in shown}:
        selected_id = shown[0]["id"] if shown else ""
    selected_flight = flight_by_id.get(selected_id)

    cargo_count = sum(flight["type"] in {"cargo", "mixed"} for flight in all_flights)
    crew_count = sum(flight["type"] in {"crew", "mixed"} for flight in all_flights)

    return {
        "utc_time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        "feed_status": feed_source,
        "feed_is_live": feed_source.startswith("LIVE"),
        "airborne_count": len(all_flights),
        "cargo_count": cargo_count,
        "crew_count": crew_count,
        "watched": sorted(watched),
        "category": category,
        "query": query,
        "flights": shown,
        "selected_id": selected_id,
        "selected_flight": selected_flight,
    }


def make_url(category: str, query: str, flight_id: str | None = None, refresh: bool = False) -> str:
    params = {"filter": category}
    if query:
        params["q"] = query
    if flight_id:
        params["flight"] = flight_id
    if refresh:
        params["refresh"] = "1"
    return "/?" + urlencode(params)


def safe_json_script(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def render_cards(flights: list[dict], category: str, query: str, selected_id: str) -> str:
    if not flights:
        return (
            '<div class="empty-state">No live aircraft matched that search.<br>'
            'Enter any exact callsign (e.g. <strong>UAL123</strong>) or tail number to query global ADS-B live.</div>'
        )
    cards = []
    for flight in flights:
        selected = flight["id"] == selected_id
        kind = flight["type"]
        tag = {"crew": "NOTABLE CREW", "mixed": "CREW + CARGO"}.get(kind, "ODD CARGO")
        extra_chips = ""
        if flight.get("is_orbiting"):
            extra_chips += ' <span class="status-chip orbit">ORBIT</span>'
        if flight.get("sua_id"):
            extra_chips += f' <span class="status-chip sua">{html.escape(flight["sua_id"])}</span>'
        if flight.get("aar_partner"):
            extra_chips += ' <span class="status-chip aar">AAR</span>'
        cards.append(
            f'<a class="flight-card{" active" if selected else ""}" '
            f'href="{html.escape(make_url(category, query, flight["id"]), quote=True)}"'
            f' data-flight-id="{html.escape(flight["id"], quote=True)}"'
            f'{" aria-current=page" if selected else ""}>'
            f'<span class="flight-topline"><span class="flight-id">{html.escape(flight["id"])} '
            f'<span class="oddity-pill">{flight["oddity_score"]}</span>{extra_chips}</span>'
            f'<span class="flight-alt">{html.escape(flight["altitude"])}</span></span>'
            f'<span class="flight-route"><span class="airport">{html.escape(flight["origin"])}</span>'
            f'<i class="route-line"></i><span class="airport">{html.escape(flight["destination"])}</span></span>'
            f'<span class="flight-reason">{html.escape(flight["reason"])}</span>'
            f'<span class="flight-aboard">{html.escape(flight["aboard"])}</span>'
            f'<span class="flight-meta"><span class="tag {kind}">{tag} · {html.escape(flight["anomaly_badge"])}</span>'
            f'<span class="flight-time">{html.escape(flight["eta"])}</span></span></a>'
        )
    return "".join(cards)


def render_detail(flight: dict | None, watched: set[str], category: str, query: str) -> str:
    if flight is None:
        return '<div class="empty-state">No flight selected. Search any callsign or pick a target from the feed.</div>'
    is_watched = flight["id"] in watched
    button_label = "★ WATCHING" if is_watched else "☆ WATCH"
    watch_form = (
        '<form id="watch-form" method="post" action="/watch">'
        f'<input type="hidden" name="flight" value="{html.escape(flight["id"], quote=True)}">'
        f'<input type="hidden" name="filter" value="{category}">'
        f'<input type="hidden" name="q" value="{html.escape(query, quote=True)}">'
        f'<button class="watch-btn{" watching" if is_watched else ""}" type="submit">'
        f'{button_label}</button></form>'
    )
    osint_links = ""
    if flight.get("hex"):
        hex_q = quote(flight["hex"])
        cs_q = quote(flight["id"])
        osint_links = (
            f'<a class="osint-link" href="https://globe.adsbexchange.com/?icao={hex_q}" target="_blank" rel="noopener">ADS-B TRACK ↗</a>'
            f'<a class="osint-link" href="https://www.flightaware.com/live/flight/{cs_q}" target="_blank" rel="noopener">FLIGHT LOG ↗</a>'
        )
    kicker_parts = [
        f"Oddity {flight['oddity_score']}/100",
        flight["anomaly_badge"],
        flight.get("unit") or flight["operator"],
    ]
    if flight.get("home_base"):
        kicker_parts.append(f"Base: {flight['home_base']}")

    return (
        '<div class="detail-head"><div>'
        f'<div class="detail-kicker">{html.escape(" · ".join(kicker_parts))}</div>'
        f'<h3>{html.escape(flight["reason"])}</h3>'
        f'<div class="detail-type">{html.escape(flight["from"])} → {html.escape(flight["to"])}</div>'
        f'</div><div class="detail-actions">{osint_links}{watch_form}</div></div>'
        f'<p class="detail-description">{html.escape(flight["detail"])}</p>'
        '<div class="detail-stats">'
        f'<div><span class="detail-stat-label">Aboard / Payload</span><span class="detail-stat-value">{html.escape(flight["aboard"])}</span></div>'
        f'<div><span class="detail-stat-label">Aircraft</span><span class="detail-stat-value">{html.escape(flight["aircraft"])}</span></div>'
        f'<div><span class="detail-stat-label">Altitude / V-Rate</span><span class="detail-stat-value">{html.escape(flight["altitude"])} ({html.escape(flight["vrate_str"])})</span></div>'
        f'<div><span class="detail-stat-label">Ground speed</span><span class="detail-stat-value">{html.escape(flight["speed"])}</span></div>'
        f'<div><span class="detail-stat-label">Est. arrival</span><span class="detail-stat-value">{html.escape(flight["eta"])}</span></div>'
        '</div>'
    )


def render_dashboard(
    category: str,
    query: str,
    selected_id: str,
    watched: set[str],
    force_refresh: bool = False,
) -> str:
    state = resolve_dashboard_state(category, query, selected_id, watched, force_refresh=force_refresh)
    cat = state["category"]
    q = state["query"]
    sel_id = state["selected_id"]

    replacements = {
        "UTC_TIME": state["utc_time"],
        "FEED_STATUS": html.escape(state["feed_status"]),
        "FEED_BADGE_CLASS": "live" if state["feed_is_live"] else "offline",
        "AIRBORNE": f"{state['airborne_count']:02d}",
        "CARGO_COUNT": f"{state['cargo_count']:02d}",
        "CREW_COUNT": f"{state['crew_count']:02d}",
        "WATCHED_COUNT": f"{len(watched):02d}",
        "FILTER": cat,
        "QUERY": html.escape(q, quote=True),
        "ALL_SELECTED": "selected" if cat == "all" else "",
        "CARGO_SELECTED": "selected" if cat == "cargo" else "",
        "CREW_SELECTED": "selected" if cat == "crew" else "",
        "ALL_URL": html.escape(make_url("all", q), quote=True),
        "CARGO_URL": html.escape(make_url("cargo", q), quote=True),
        "CREW_URL": html.escape(make_url("crew", q), quote=True),
        "CHIP_CLEAR_URL": html.escape(make_url(cat, ""), quote=True),
        "CHIP_OUTSIZE_URL": html.escape(make_url("cargo", "outsize"), quote=True),
        "CHIP_SUA_URL": html.escape(make_url("all", "SUA"), quote=True),
        "CHIP_AAR_URL": html.escape(make_url("all", "REFUELING"), quote=True),
        "CHIP_ORBIT_URL": html.escape(make_url("all", "orbit"), quote=True),
        "CHIP_PIA_URL": html.escape(make_url("all", "PIA"), quote=True),
        "REFRESH_URL": html.escape(make_url(cat, q, sel_id, refresh=True), quote=True),
        "FLIGHT_CARDS": render_cards(state["flights"], cat, q, sel_id),
        "FLIGHT_DETAIL": render_detail(state["selected_flight"], watched, cat, q),
        "FLIGHTS_JSON": safe_json_script(state["flights"]),
        "SELECTED_ID_JSON": safe_json_script(sel_id),
        "WATCHED_JSON": safe_json_script(state["watched"]),
        "SUA_JSON": safe_json_script(list(SPECIAL_USE_AIRSPACES)),
        "FILTER_JSON": safe_json_script(cat),
        "QUERY_JSON": safe_json_script(q),
    }
    return TEMPLATE.substitute(replacements)


class StrangeCrewHandler(BaseHTTPRequestHandler):
    def read_watchlist(self) -> set[str]:
        cookie = cookies.SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except cookies.CookieError:
            logging.warning("Ignoring malformed watchlist cookie from %s", self.client_address[0])
            return set()
        value = cookie.get(COOKIE_NAME)
        if value is None:
            return set()
        flight_ids = unquote(value.value).split(",")
        return {fid for fid in flight_ids if FLIGHT_ID_RE.match(fid)}

    def send_json(self, payload: dict, set_cookie: str | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if set_cookie is not None:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def send_dashboard(
        self,
        category: str,
        query: str,
        selected_id: str,
        watched: set[str],
        force_refresh: bool = False,
        set_cookie: str | None = None,
    ) -> None:
        body = render_dashboard(
            category, query, selected_id, watched, force_refresh=force_refresh
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if set_cookie is not None:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        params = parse_qs(parsed.query)
        category = params.get("filter", ["all"])[0]
        query = params.get("q", [""])[0]
        selected_id = params.get("flight", [""])[0]
        force_refresh = params.get("refresh", ["0"])[0] == "1"
        watched = self.read_watchlist()

        if parsed.path == "/api/flights":
            state = resolve_dashboard_state(category, query, selected_id, watched, force_refresh=force_refresh)
            self.send_json({
                "utc_time": state["utc_time"],
                "feed_status": state["feed_status"],
                "feed_is_live": state["feed_is_live"],
                "airborne_count": state["airborne_count"],
                "cargo_count": state["cargo_count"],
                "crew_count": state["crew_count"],
                "watched": state["watched"],
                "category": state["category"],
                "query": state["query"],
                "selected_id": state["selected_id"],
                "flights": state["flights"],
            })
            return

        if parsed.path != "/":
            self.send_error(404, "Not found")
            return

        self.send_dashboard(category, query, selected_id, watched, force_refresh=force_refresh)

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/watch":
            self.send_error(404, "Not found")
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400, "Invalid request")
            return
        if content_length > 8192:
            self.send_error(413, "Request too large")
            return
        params = parse_qs(self.rfile.read(content_length).decode("utf-8", errors="replace"))
        selected_id = params.get("flight", [""])[0].strip().upper()
        if not FLIGHT_ID_RE.match(selected_id):
            self.send_error(400, "Invalid flight identifier")
            return
        category = params.get("filter", ["all"])[0]
        query = params.get("q", [""])[0][:100]
        watched = self.read_watchlist()
        if selected_id in watched:
            watched.remove(selected_id)
        else:
            watched.add(selected_id)
        cookie = cookies.SimpleCookie()
        cookie[COOKIE_NAME] = quote(",".join(sorted(watched)), safe="")
        cookie[COOKIE_NAME]["path"] = "/"
        cookie[COOKIE_NAME]["max-age"] = 60 * 60 * 24 * 365
        cookie[COOKIE_NAME]["httponly"] = True
        cookie[COOKIE_NAME]["samesite"] = "Lax"

        if "application/json" in self.headers.get("Accept", ""):
            self.send_json(
                {"watched": sorted(watched), "selected_id": selected_id},
                set_cookie=cookie[COOKIE_NAME].OutputString(),
            )
            return

        self.send_dashboard(category, query, selected_id, watched, set_cookie=cookie[COOKIE_NAME].OutputString())

    def log_message(self, format_string: str, *args: object) -> None:
        logging.info("%s - %s", self.address_string(), format_string % args)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Strange Crew live flight dashboard.")
    parser.add_argument("--host", default="0.0.0.0", help="interface to listen on (default: all interfaces)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    with ThreadingHTTPServer((args.host, args.port), StrangeCrewHandler) as server:
        logging.info("Strange Crew is listening at http://%s:%s", args.host, args.port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logging.info("Shutting down")


if __name__ == "__main__":
    main()