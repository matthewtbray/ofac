import xml.etree.ElementTree as ET

tree = ET.parse('sdn_advanced.xml')
root = tree.getroot()

# Must include the namespace in every tag query
ns = {'s': 'https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ADVANCED_XML'}

# Build lookup dictionaries from ReferenceValueSets
country_lookup = {}
for country in root.findall('.//s:CountryValues/s:Country', ns):
    country_id = country.get('ID')
    country_lookup[country_id] = country.text

area_code_lookup = {}
for area in root.findall('.//s:AreaCodeValues/s:AreaCode', ns):
    area_id = area.get('ID')
    area_code_lookup[area_id] = area.get('Description', area.text or '')

# Parse Locations using the lookups
for location in root.findall('.//s:Location', ns):
    loc_id = location.get('ID')

    area_code_elem = location.find('s:LocationAreaCode', ns)
    country_elem = location.find('s:LocationCountry', ns)

    area_code_id = area_code_elem.get('AreaCodeID') if area_code_elem is not None else None
    country_id = country_elem.get('CountryID') if country_elem is not None else None

    country_name = country_lookup.get(country_id, 'Unknown')
    area_desc = area_code_lookup.get(area_code_id, 'Unknown')

    print(f"Location {loc_id}: {country_name} ({area_desc})")
