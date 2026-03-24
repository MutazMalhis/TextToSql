# parse_schema.py
from schema_parser import SchemaDocumentParser

# Read your schema document
with open('schema_document.txt', 'r', encoding='utf-8') as f:
    schema_text = f.read()

# Parse and save
parser = SchemaDocumentParser(schema_text)
tables = parser.parse_schema()
parser.save_to_json('erp_schema.json')

print(f"Parsed {len(tables)} tables")
print("Schema saved to erp_schema.json")