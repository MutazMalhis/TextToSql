import os
import json
import re
from typing import List, Dict, Optional, Tuple, Set
from dataclasses import dataclass, asdict
from datetime import datetime
import openai
from openai import OpenAI
import sqlite3
import logging
from collections import defaultdict
import pickle
import sqlglot
from sqlglot import parse_one, exp


# -----------------------------------------------------------------------------
# LLM / Model configuration (local Qwen via vLLM)
# -----------------------------------------------------------------------------
# These can be overridden in your .env or server environment:
#   LLM_BASE_URL       -> Where your local vLLM / Qwen server is running
#   LLM_MODEL_NAME     -> Default model name served by that endpoint
# -----------------------------------------------------------------------------

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:8080/v1")
LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "Qwen/Qwen3-Coder-30B-A3B-Instruct")



# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class TableInfo:
    """Represents a database table with its metadata"""
    name: str
    description: str
    columns: List[Dict[str, str]]
    primary_keys: List[str]
    foreign_keys: List[Dict[str, str]]
    domain: str
    keywords: List[str]

@dataclass
class ConversationMemory:
    """Stores conversation context and history"""
    conversation_id: str
    messages: List[Dict[str, str]]
    used_tables: List[str]
    user_preferences: Dict[str, str]
    last_query: Optional[str]
    created_at: datetime
    updated_at: datetime

class SQLValidator:
    """Validates SQL queries against schema"""
    
    def __init__(self, schema_processor):
        self.schema_processor = schema_processor
    
    def extract_table_columns(self, sql: str) -> Dict[str, List[str]]:
        """Extract tables and their columns from SQL using sqlglot"""
        table_columns = defaultdict(set)
        
        try:
            # Parse the SQL - handle Oracle dialect
            parsed = parse_one(sql, dialect='oracle')
            
            # Find all column references
            for column in parsed.find_all(exp.Column):
                table_name = column.table
                column_name = column.name
                
                if table_name and column_name:
                    # Remove schema prefix if present
                    if '.' in table_name:
                        table_name = table_name.split('.')[-1]
                    
                    # Skip system columns
                    if column_name.upper() not in ['*', 'ROWNUM']:
                        table_columns[table_name.upper()].add(column_name.upper())
            
            # Extract table aliases
            table_aliases = {}
            for table in parsed.find_all(exp.Table):
                table_name = table.name
                alias = table.alias if hasattr(table, 'alias') and table.alias else None
                
                # Remove schema prefix
                if '.' in table_name:
                    table_name = table_name.split('.')[-1]
                
                if alias:
                    table_aliases[alias.upper()] = table_name.upper()
            
            # Resolve aliases to actual table names
            resolved_columns = defaultdict(set)
            for table_ref, columns in table_columns.items():
                actual_table = table_aliases.get(table_ref, table_ref)
                resolved_columns[actual_table].update(columns)
            
            # Convert sets to lists
            return {table: list(columns) for table, columns in resolved_columns.items()}
        
        except Exception as e:
            logger.error(f"Error parsing SQL with sqlglot: {e}")
            return self._fallback_extraction(sql)
    
    def _fallback_extraction(self, sql: str) -> Dict[str, List[str]]:
        """Fallback method using regex if sqlglot fails"""
        table_columns = defaultdict(set)
        
        # Find table.column patterns
        pattern = r'([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)'
        matches = re.findall(pattern, sql)
        
        for table, column in matches:
            if column.upper() != '*':
                table_columns[table.upper()].add(column.upper())
        
        return {table: list(columns) for table, columns in table_columns.items()}
    
    def validate_columns(self, sql: str, relevant_tables: List[TableInfo]) -> Tuple[bool, Dict[str, List[str]]]:
        """
        Validate that all columns in SQL exist in the schema.
        
        Returns:
            Tuple of (is_valid, invalid_columns_dict)
            invalid_columns_dict maps table_name -> list of invalid column names
        """
        # Extract tables and columns from SQL
        sql_table_columns = self.extract_table_columns(sql)
        
        # Build a lookup of valid columns per table
        valid_columns = {}
        for table in relevant_tables:
            table_name_upper = table.name.upper()
            valid_columns[table_name_upper] = {col['name'].upper() for col in table.columns}
        
        # Check for invalid columns
        invalid_columns = defaultdict(list)
        
        for table_name, columns in sql_table_columns.items():
            
            if table_name not in valid_columns and table_name not in ["IAS_RT_BILL_MST"]:
                logger.warning(f"Table {table_name} not found in schema")
                continue
            
            for column in columns:
                if column not in valid_columns[table_name]:
                    invalid_columns[table_name].append(column)
        
        is_valid = len(invalid_columns) == 0
        
        print("is_valid", is_valid)
        print("invalid columns", dict(invalid_columns))
        return is_valid, dict(invalid_columns)
    
    def format_validation_error(self, invalid_columns: Dict[str, List[str]], 
                               relevant_tables: List[TableInfo]) -> str:
        """Format validation error message for the model"""
        error_msg = "The SQL query contains invalid columns that do not exist in the database schema:\n\n"
        
        for table_name, columns in invalid_columns.items():
            error_msg += f"Table '{table_name}':\n"
            error_msg += f"  Invalid columns: {', '.join(columns)}\n"
            
            # Find the table and suggest valid columns
            for table in relevant_tables:
                if table.name.upper() == table_name:
                    valid_cols = [col['name'] for col in table.columns]
                    error_msg += f"  Valid columns: {', '.join(valid_cols[:])}\n"
                    break
            error_msg += "\n"
        
        error_msg += "Please regenerate the SQL query using only the valid columns from the schema."
        return error_msg

class SchemaProcessor:
    """Processes and filters the database schema"""
    
    def __init__(self):
        self.tables = {}
        self.domain_mapping = {
            'sales': ['IAS_BILL', 'ARS_BILL', 'IAS_RT_BILL', 'OTHER_CHARGES', 'SALES_'],
            'customer': ['CUSTOMER', 'IAS_CASH_CUSTMR'],
            'inventory': ['IAS_ITM', 'IAS_CONN_ITM', 'IAS_ITEM'],
            'tax': ['GNR_TAX'],
            'financial': ['EX_RATE', 'ACCOUNT', 'INSTALLMENT'],
            'insurance': ['IAS_INSRNCE'],
            'employees': ['S_EMP', 'SALES_MAN', 'COLLERCTOR'],
            'logistics': ['IAS_DRIVERS', 'IAS_CARGO']
        }
        self.load_schema()
    
    def load_schema(self):
        """Load and process the schema from the document"""
        schema_data = self._parse_schema_document()
        
        for table_data in schema_data:
            table = TableInfo(
                name=table_data['name'],
                description=table_data['description'],
                columns=table_data['columns'],
                primary_keys=table_data['primary_keys'],
                foreign_keys=table_data['foreign_keys'],
                domain=self._classify_domain(table_data['name']),
                keywords=self._extract_keywords(table_data)
            )
            self.tables[table.name] = table
    
    def _parse_schema_document(self):
        """Parse the schema document"""
        with open('erp_schema.json', 'r') as f:
            schema_data = json.load(f)
        return schema_data
    
    def _classify_domain(self, table_name: str) -> str:
        """Classify table into business domain"""
        for domain, prefixes in self.domain_mapping.items():
            if any(table_name.startswith(prefix) for prefix in prefixes):
                return domain
        return 'general'
    
    def _extract_keywords(self, table_data: Dict) -> List[str]:
        """Extract keywords from table description and columns"""
        keywords = []
        
        desc = table_data.get('description', '')
        keywords.extend(desc.lower().split())
        
        for col in table_data.get('columns', []):
            col_desc = col.get('description', '')
            english_col_desc = re.sub(r'[\u0600-\u06FF]+', '', col_desc).strip()
            keywords.extend(english_col_desc.lower().split())
        
        return list(set([kw for kw in keywords if len(kw) > 2]))
    
    def filter_relevant_tables(self, query: str, max_tables: int = 6) -> List["TableInfo"]:
        """
        Always return core sales tables (4) + up to 2 additional relevant tables (scored).
        Total output size defaults to 6 unless fewer tables exist in schema.

        - Supports Arabic/English keywords (augmented query).
        - Never returns duplicates.
        - Works whether self.tables is dict[str, TableInfo] or list[TableInfo] or list[str] keys.
        """

        CORE_SALES_TABLES = [
            "IAS_BILL_MST",
            "IAS_BILL_DTL",
            "IAS_RT_BILL_MST",
            "IAS_RT_BILL_DTL",
        ]

        EXTRA_TABLES_COUNT = 2  # <-- your requirement: 2 additional tables

        # ------------------------------------------------------------
        # 1) Normalize tables into: tables_by_name + tables_list
        # ------------------------------------------------------------
        tables_by_name: dict[str, TableInfo] = {}

        if isinstance(self.tables, dict):
            # {"IAS_BILL_MST": TableInfo(...), ...}
            for k, v in self.tables.items():
                if isinstance(v, TableInfo) and getattr(v, "name", None):
                    tables_by_name[v.name.upper()] = v
                elif isinstance(v, TableInfo) and isinstance(k, str):
                    tables_by_name[k.upper()] = v

        elif isinstance(self.tables, list):
            # could be [TableInfo(...), ...] or ["IAS_BILL_MST", ...]
            for obj in self.tables:
                if isinstance(obj, TableInfo) and getattr(obj, "name", None):
                    tables_by_name[obj.name.upper()] = obj
                elif isinstance(obj, str):
                    # optional fallback if you have a dict stored elsewhere
                    fallback_dict = getattr(self, "tables_dict", None)
                    if isinstance(fallback_dict, dict):
                        ti = fallback_dict.get(obj) or fallback_dict.get(obj.upper())
                        if isinstance(ti, TableInfo) and getattr(ti, "name", None):
                            tables_by_name[ti.name.upper()] = ti
        else:
            try:
                for obj in self.tables:  # type: ignore[assignment]
                    if isinstance(obj, TableInfo) and getattr(obj, "name", None):
                        tables_by_name[obj.name.upper()] = obj
            except Exception:
                tables_by_name = {}

        if not tables_by_name:
            return []

        tables_list: list[TableInfo] = list(tables_by_name.values())

        # ------------------------------------------------------------
        # 2) Always include the 4 core tables (if present)
        # ------------------------------------------------------------
        selected_by_name: dict[str, TableInfo] = {}
        for tname in CORE_SALES_TABLES:
            ti = tables_by_name.get(tname.upper())
            if isinstance(ti, TableInfo):
                selected_by_name[tname.upper()] = ti

        # ------------------------------------------------------------
        # 3) Build augmented query (Arabic expansions)
        # ------------------------------------------------------------
        query_lower = (query or "").lower().strip()

        arabic_to_english = {
            "فاتورة": "invoice",
            "فواتير": "invoice",
            "مبيعات": "sales",
            "بيع": "sales",
            "مردود": "return",
            "مرتجع": "return",
            "مرتجعات": "return",
            "ترجيع": "return",
            "زبون": "customer",
            "عميل": "customer",
            "زبائن": "customers",
            "عملاء": "customers",
            "مورد": "vendor",
            "موردين": "vendors",
            "صنف": "item",
            "أصناف": "items",
            "منتج": "product",
            "منتجات": "products",
            "مخزون": "stock",
            "بضاعة": "inventory",
            "ضريبة": "tax",
            "ضرائب": "taxes",
            "عملة": "currency",
            "عملات": "currencies",
            "دفعة": "payment",
            "دفعات": "payments",
        }

        extra_terms: list[str] = []
        for ar, en in arabic_to_english.items():
            if ar in query_lower:
                extra_terms.append(en)

        augmented_query = " ".join([query_lower] + extra_terms).strip()

        # If query is empty: core + first 2 non-core
        if not augmented_query:
            needed = min(EXTRA_TABLES_COUNT, max(0, max_tables - len(selected_by_name)))
            for t in tables_list:
                tn = t.name.upper()
                if tn in selected_by_name:
                    continue
                selected_by_name[tn] = t
                needed -= 1
                if needed <= 0:
                    break
            return list(selected_by_name.values())[:max_tables]

        # ------------------------------------------------------------
        # 4) Score other tables to pick +2 additional
        # ------------------------------------------------------------
        domain_keywords = {
            "sales": ["sales", "invoice", "bill", "فاتورة", "فواتير", "مبيعات"],
            "customer": ["customer", "client", "زبون", "عميل", "زبائن", "عملاء"],
            "inventory": ["item", "product", "stock", "inventory", "صنف", "أصناف", "منتج", "منتجات", "مخزون", "بضاعة"],
            "tax": ["tax", "vat", "ضريبة", "ضرائب"],
            "financial": ["payment", "receipt", "cash", "bank", "دفعة", "دفعات", "سند", "شيك"],
        }

        query_tokens = [w for w in augmented_query.split() if w]

        scored: list[tuple[int, TableInfo]] = []
        for table in tables_list:
            tname = table.name.upper()

            # skip already-selected core
            if tname in selected_by_name:
                continue

            score = 0
            name_lower = table.name.lower()

            # domain boost
            dom = getattr(table, "domain", None)
            if dom and dom in domain_keywords:
                keys = domain_keywords[dom]
                if any(k in augmented_query for k in keys):
                    score += 4

            # name match boost
            if any(tok in name_lower for tok in query_tokens):
                score += 2

            # keywords boost
            kws = getattr(table, "keywords", None)
            if kws:
                for kw in kws:
                    kw_lower = str(kw).lower().strip()
                    if kw_lower and kw_lower in augmented_query:
                        score += 1

            if score > 0:
                scored.append((score, table))

        scored.sort(key=lambda x: x[0], reverse=True)

        # ------------------------------------------------------------
        # 5) Add exactly up to 2 scored tables (then fill if needed)
        # ------------------------------------------------------------
        extra_slots = min(EXTRA_TABLES_COUNT, max(0, max_tables - len(selected_by_name)))

        for _, t in scored:
            if extra_slots <= 0:
                break
            tn = t.name.upper()
            if tn in selected_by_name:
                continue
            selected_by_name[tn] = t
            extra_slots -= 1

        # Fill remaining extra slots if scoring found fewer than 2
        if extra_slots > 0:
            for t in tables_list:
                tn = t.name.upper()
                if tn in selected_by_name:
                    continue
                selected_by_name[tn] = t
                extra_slots -= 1
                if extra_slots <= 0:
                    break

        return list(selected_by_name.values())[:max_tables]




class MemoryManager:
    """Manages conversation memory and context"""
    
    def __init__(self, storage_path: str = "conversation_memory.pkl"):
        self.storage_path = storage_path
        self.conversations = {}
        self.load_memory()
    
    def load_memory(self):
        """Load conversation memory from storage"""
        try:
            if os.path.exists(self.storage_path):
                with open(self.storage_path, 'rb') as f:
                    self.conversations = pickle.load(f)
        except Exception as e:
            logger.warning(f"Could not load memory: {e}")
            self.conversations = {}
    
    def save_memory(self):
        """Save conversation memory to storage"""
        try:
            with open(self.storage_path, 'wb') as f:
                pickle.dump(self.conversations, f)
        except Exception as e:
            logger.error(f"Could not save memory: {e}")
    
    def get_or_create_conversation(self, conversation_id: str) -> ConversationMemory:
        """Get existing conversation or create new one"""
        if conversation_id not in self.conversations:
            self.conversations[conversation_id] = ConversationMemory(
                conversation_id=conversation_id,
                messages=[],
                used_tables=[],
                user_preferences={},
                last_query=None,
                created_at=datetime.now(),
                updated_at=datetime.now()
            )
        return self.conversations[conversation_id]
    
    def add_message(self, conversation_id: str, role: str, content: str, sql_query: str = None):
        """Add message to conversation memory"""
        conversation = self.get_or_create_conversation(conversation_id)
        
        message = {
            'role': role,
            'content': content,
            'timestamp': datetime.now().isoformat()
        }
        
        if sql_query:
            message['sql_query'] = sql_query
        
        conversation.messages.append(message)
        conversation.updated_at = datetime.now()
        
        if sql_query:
            conversation.last_query = sql_query
        
        self.save_memory()
    
    def get_conversation_context(self, conversation_id: str, max_messages: int = 10) -> List[Dict]:
        """Get recent conversation context"""
        conversation = self.get_or_create_conversation(conversation_id)
        return conversation.messages[-max_messages:]

class TextToSQLGenerator:
    """Main class for generating SQL from natural language (Oracle 12c + local Qwen)"""
    
    def __init__(self, api_key: str, model: Optional[str] = None):
        """
        Initialize the Text-to-SQL generator.

        Parameters
        ----------
        api_key : str
            For local Qwen via vLLM this is usually not needed, but we keep it
            so the same class can be reused with cloud models if required.
        model : Optional[str]
            Overrides the default model name. If None, falls back to
            LLM_MODEL_NAME from environment or the hard-coded default.
        """
        # Decide which model name to use
        effective_model = model or LLM_MODEL_NAME

        # Create OpenAI-compatible client for local Qwen (vLLM server)
        # - base_url is configurable via LLM_BASE_URL env var
        # - api_key is usually ignored by vLLM, but we pass something anyway
        self.client = OpenAI(
            api_key=api_key or "not-needed",
            base_url=LLM_BASE_URL,
        )
        self.model = effective_model

        # Core subsystems
        self.schema_processor = SchemaProcessor()
        self.memory_manager = MemoryManager()
        self.validator = SQLValidator(self.schema_processor)

        # How many times to retry when SQL validation fails
        self.max_retries = 3

    def _create_schema_context(self, relevant_tables: List[TableInfo]) -> str:
        """Create schema context for the prompt"""
        context = "Database Schema Information:\n\n"
        
        for table in relevant_tables:
            context += f"Table: {table.name}\n"
            context += f"Description: {table.description}\n"
            context += "Columns:\n"
            
            for col in table.columns[:10]:
                nullable = "NULL" if col.get('nullable', True) else "NOT NULL"
                context += f"  - {col['name']} ({col['type']}) [{nullable}] - {col.get('description', '')}\n"
            
            if table.foreign_keys:
                context += "Foreign Keys:\n"
                for fk in table.foreign_keys:
                    if 'local_columns' in fk and 'foreign_table' in fk and 'foreign_columns' in fk:
                        local_cols = ', '.join(fk['local_columns'])
                        foreign_cols = ', '.join(fk['foreign_columns'])
                        foreign_table = fk['foreign_table']
                        context += f"  - {local_cols} → {foreign_table}({foreign_cols})\n"
                    elif 'references' in fk:
                        context += f"  - {fk.get('column', 'Unknown')} → {fk['references']}\n"
                    else:
                        column = fk.get('column', 'Unknown')
                        references = fk.get('references', 'Unknown')
                        context += f"  - {column} → {references}\n"
            
            context += "\n"
        
        return context
    
    def _create_system_prompt(self) -> str:
        """
        System prompt for SQL generation (Oracle 12c + ERP business rules + client style guide).
        """
        return """
You are an expert Oracle Database 12c SQL generator working with a specific ERP schema (IAS202538).
Your job is to produce ONE valid SQL query that directly answers the user’s business question.
Follow the business logic, formulas, naming conventions, and style patterns EXACTLY as specified below.

=======================================================================
[CRITICAL BEHAVIOR RULES]

1. ALWAYS output a single SQL query with NO explanations.
2. NEVER wrap SQL in ```sql``` or backticks.
3. NEVER invent table names or column names.
4. ALWAYS qualify columns with table aliases (M., D., RM., RD.).
    4.1 COLUMN AMBIGUITY RULE (STRICT):
    - Any column that appears in more than one table (e.g., W_CODE, BILL_DATE, C_CODE)
    MUST be fully qualified with its table alias.
    - NEVER use unqualified column names in SELECT, GROUP BY, ORDER BY, or WHERE.
    - If a column exists in both header and detail tables:
        - Prefer D.<column> when grouping or aggregating item-level data.
        - Prefer M.<column> only when the business meaning is clearly header-level.
    - Queries that use unqualified columns are INVALID.
5. ALWAYS use Oracle 12c syntax only (FETCH FIRST, OFFSET/FETCH, TRUNC, NVL, CASE, EXTRACT…).
6. When the user asks in Arabic, the meaning is already translated for you.

7. RETURNS / NET SALES GATING (MANDATORY):

   7.1 KPI OVERRIDE (HIGHEST PRIORITY):
       - If the user asks for "sales indicators" OR Arabic equivalents like:
         "مؤشرات المبيعات", "مؤشر المبيعات", "KPIs", "kpi"
         then you MUST return a single SQL statement that includes:
           TOTAL_SALES, TOTAL_RETURNS, NET_SALES
         using the approved formulas and returns tables.
         TOTAL_RETURNS must be computed from IAS_RT_* tables (must NOT be 0).

   7.2 Otherwise:
       - ONLY include returns tables (IAS_RT_*) or returns formulas when the user explicitly asks.
       - Explicit returns keywords include: "مردود", "مردودات", "مرتجع", "مرتجعات", "إرجاع", "returns", "return".
       - Explicit net keywords include: "صافي", "صافى", "net", "net sales".
       - If the user did NOT ask returns or net sales, return ONLY total sales.

    7.3 RETURNS-ONLY OVERRIDE (HIGH PRIORITY):
    - If the user asks for returns ONLY (Arabic: "مردود", "مردود المبيعات", "مرتجعات", "المردودات")
      and DOES NOT ask for net ("صافي", "net", "net sales"),
      then return ONLY:
        PERIOD (if requested) + TOTAL_RETURNS
      using ONLY IAS202538.IAS_RT_BILL_MST RM and IAS202538.IAS_RT_BILL_DTL RD.

    - DO NOT compute TOTAL_SALES or NET_SALES in this case.
    - If quarter is requested, PERIOD must be:
        TO_CHAR(RM.RT_BILL_DATE, 'YYYY-"Q"Q')


8. NEVER schema-qualify Oracle built-ins or aliases.
   - Valid:  FROM IAS202538.IAS_BILL_MST M
   - Valid:  M.BILL_DATE, D.I_PRICE, SYSDATE, TRUNC(SYSDATE,'MM'), NVL(...), EXTRACT(...)
   - Invalid: IAS202538.M.BILL_DATE
   - Invalid: IAS202538.SYSDATE
   - Invalid: IAS202538.TRUNC(...), IAS202538.NVL(...), IAS202538.EXTRACT(...)

9. Schema prefix rule (STRICT):
   - Tables MUST be schema-qualified: IAS202538.<TABLE_NAME>
   - Columns, aliases, and built-in functions MUST NOT be schema-qualified.

=======================================================================


=======================================================================
[STANDARD TABLE ALIASES — ALWAYS USE]

IAS_BILL_MST         → M
IAS_BILL_DTL         → D
IAS_RT_BILL_MST      → RM
IAS_RT_BILL_DTL      → RD

=======================================================================


=======================================================================
[ERP BUSINESS LOGIC FORMULAS (CLIENT-APPROVED)]

1) Sales Amount per line (TOTAL SALES)
   SALES_FORMULA =
       (NVL(D.I_PRICE, 0) - NVL(D.DIS_AMT, 0) + NVL(D.OTHR_AMT, 0))
       * NVL(D.I_QTY, 0)
       * NVL(M.BILL_RATE, 1)

2) Returns Amount per line (TOTAL RETURNS) — ONLY WHEN REQUESTED
   RETURNS_FORMULA =
       (NVL(RD.I_PRICE, 0) - NVL(RD.DIS_AMT, 0) + NVL(RD.OTHR_AMT, 0))
       * NVL(RD.I_QTY, 0)
       * NVL(RM.RT_BILL_RATE, 1)

3) Net Sales (ONLY WHEN EXPLICITLY REQUESTED)
   Acceptable patterns:

   A) Direct subtraction using separate aggregates (ONLY if you are NOT forced to avoid join):
       SUM(SALES_FORMULA) - SUM(RETURNS_FORMULA)

   B) UNION ALL (client-preferred grouping logic):
       SELECT period_col,
              SUM(NET_SALES - RT_NET_SALES) AS NET_SALES
       FROM (
           SELECT <period_col> AS period_col,
                  SALES_FORMULA AS NET_SALES,
                  0 AS RT_NET_SALES
           FROM IAS202538.IAS_BILL_MST M
           JOIN IAS202538.IAS_BILL_DTL D
             ON M.BILL_DOC_TYPE = D.BILL_DOC_TYPE
            AND M.BILL_NO   = D.BILL_NO
            AND M.BILL_SER  = D.BILL_SER

           UNION ALL

           SELECT <period_col> AS period_col,
                  0 AS NET_SALES,
                  RETURNS_FORMULA AS RT_NET_SALES
           FROM IAS202538.IAS_RT_BILL_MST RM
           JOIN IAS202538.IAS_RT_BILL_DTL RD
             ON RM.BILL_DOC_TYPE = RD.BILL_DOC_TYPE
            AND RM.BILL_NO   = RD.BILL_NO
            AND RM.BILL_SER  = RD.BILL_SER
       )
       GROUP BY period_col;

4) Sales Indicators (HARD RULE — NO EXCEPTIONS):

    When the user asks about:
    - "مؤشرات المبيعات"
    - "مؤشر المبيعات"
    - "sales indicators"
    - "KPIs"

    YOU MUST:

    1. Use BOTH sales tables AND returns tables:
    - IAS202538.IAS_BILL_MST / IAS202538.IAS_BILL_DTL
    - IAS202538.IAS_RT_BILL_MST / IAS202538.IAS_RT_BILL_DTL

    2. TOTAL_RETURNS MUST be computed from IAS_RT_* tables.
    - Setting TOTAL_RETURNS = 0 is STRICTLY FORBIDDEN.

    3. NET_SALES MUST be computed as:
    TOTAL_SALES - TOTAL_RETURNS

    4. The ONLY allowed structure is ONE of the following:
    A) UNION ALL (preferred)
    B) Separate subqueries sourcing sales and returns independently

    5. If returns tables are missing OR TOTAL_RETURNS = 0,
    the SQL is INVALID and MUST be corrected.
    
    6. When user asks مؤشرات المبيعات / sales indicators: MUST use UNION ALL (sales stream + returns stream) and aggregate by period (or no period if not requested). FORBIDDEN: LEFT JOIN sales-to-returns
    7. Do NOT return two rows per period. The query must return one row per period by using UNION ALL inside a subquery then GROUP BY PERIOD in the outer query

=======================================================================

CANONICAL SALES INDICATORS SHAPE (MUST FOLLOW):

If period is required (e.g., monthly), use:
SELECT PERIOD,
       SUM(TOTAL_SALES) AS TOTAL_SALES,
       SUM(TOTAL_RETURNS) AS TOTAL_RETURNS,
       SUM(TOTAL_SALES) - SUM(TOTAL_RETURNS) AS NET_SALES
FROM (
    SELECT TO_CHAR(M.BILL_DATE,'YYYY-MM') AS PERIOD,
           SUM(SALES_FORMULA) AS TOTAL_SALES,
           0 AS TOTAL_RETURNS
    FROM IAS202538.IAS_BILL_MST M
    JOIN IAS202538.IAS_BILL_DTL D ON (sales join)
    <optional filters>
    GROUP BY TO_CHAR(M.BILL_DATE,'YYYY-MM')

    UNION ALL

    SELECT TO_CHAR(RM.RT_BILL_DATE,'YYYY-MM') AS PERIOD,
           0 AS TOTAL_SALES,
           SUM(RETURNS_FORMULA) AS TOTAL_RETURNS
    FROM IAS202538.IAS_RT_BILL_MST RM
    JOIN IAS202538.IAS_RT_BILL_DTL RD ON (returns join using RT_BILL_* keys)
    <optional filters>
    GROUP BY TO_CHAR(RM.RT_BILL_DATE,'YYYY-MM')
)
GROUP BY PERIOD
ORDER BY PERIOD;

IMPORTANT: Do NOT output the inner UNION ALL result directly.
You MUST wrap it and aggregate, otherwise you will produce two rows per period.


=======================================================================
[JOIN RULES — STRICT ENFORCEMENT]

Sales join:
    M.BILL_DOC_TYPE = D.BILL_DOC_TYPE
    AND M.BILL_NO   = D.BILL_NO
    AND M.BILL_SER  = D.BILL_SER

Returns join:
    RM.RT_BILL_DOC_TYPE = RD.RT_BILL_DOC_TYPE
    AND RM.RT_BILL_NO   = RD.RT_BILL_NO
    AND RM.RT_BILL_SER  = RD.RT_BILL_SER

IMPORTANT:
- Do NOT join sales and returns using LEFT JOIN as a default pattern.
- If net sales is requested, prefer UNION ALL (pattern above) unless the question forces another safe approach.

=======================================================================


=======================================================================
[CRITICAL COLUMN NAMES — DO NOT CHANGE]

OTHR_AMT       — NOT OTH_AMT / OTHER_AMT
DIS_AMT        — NOT DISC_AMT / DISCOUNT_AMT
I_PRICE        — NOT PRICE / ITEM_PRICE
I_QTY          — NOT QTY / QUANTITY
BILL_RATE      — NOT RATE
RT_BILL_RATE   — NOT RT_RATE

=======================================================================


=======================================================================
[DATE LOGIC RULES]

Use these:

“this month”             → TRUNC(SYSDATE, 'MM')
“last month”             → ADD_MONTHS(TRUNC(SYSDATE,'MM'), -1)
“last 6 months”          → BILL_DATE >= ADD_MONTHS(TRUNC(SYSDATE,'MM'), -6)
“this year”              → TRUNC(SYSDATE,'YYYY') to filter dates, or EXTRACT(YEAR FROM SYSDATE) for comparisons
“quarter”                → TO_CHAR(<date_col>, 'Q')
“exclude last quarter”   → <date_col> < TRUNC(SYSDATE, 'Q')

Important:
- SYSDATE is a built-in (no schema prefix).
- Use TRUNC(SYSDATE,'YYYY') or EXTRACT(YEAR FROM SYSDATE) correctly.

Current date context for interpreting questions: December 2025

=======================================================================


=======================================================================
[PAGINATION / TOP-N RULES — ORACLE 12c ONLY]

Correct:
    ORDER BY col FETCH FIRST n ROWS ONLY
    OFFSET x ROWS FETCH NEXT y ROWS ONLY

Forbidden:
    LIMIT
    TOP
    IIF
    OFFSET without FETCH

=======================================================================


=======================================================================
[FEW-SHOT EXAMPLES — FOLLOW THIS STYLE]

Example 1 — Monthly Total Sales (NO returns unless asked):
SELECT TO_CHAR(M.BILL_DATE, 'YYYY-MM') AS PERIOD,
       SUM(
           (NVL(D.I_PRICE,0)-NVL(D.DIS_AMT,0)+NVL(D.OTHR_AMT,0))
           * NVL(D.I_QTY,0)
           * NVL(M.BILL_RATE,1)
       ) AS TOTAL_SALES
FROM IAS202538.IAS_BILL_MST M
JOIN IAS202538.IAS_BILL_DTL D
  ON M.BILL_DOC_TYPE=D.BILL_DOC_TYPE
 AND M.BILL_NO=D.BILL_NO
 AND M.BILL_SER=D.BILL_SER
GROUP BY TO_CHAR(M.BILL_DATE,'YYYY-MM')
ORDER BY PERIOD;


Example 2 — Net Sales (only when explicitly requested; UNION ALL pattern):
SELECT PERIOD,
       SUM(NET_SALES - RT_NET_SALES) AS NET_SALES
FROM (
    SELECT TO_CHAR(M.BILL_DATE,'YYYY-MM') AS PERIOD,
           (NVL(D.I_PRICE,0)-NVL(D.DIS_AMT,0)+NVL(D.OTHR_AMT,0))
           * NVL(D.I_QTY,0)
           * NVL(M.BILL_RATE,1) AS NET_SALES,
           0 AS RT_NET_SALES
    FROM IAS202538.IAS_BILL_MST M
    JOIN IAS202538.IAS_BILL_DTL D
    ON RM.RT_BILL_DOC_TYPE = RD.RT_BILL_DOC_TYPE
    AND RM.RT_BILL_NO       = RD.RT_BILL_NO
    AND RM.RT_BILL_SER      = RD.RT_BILL_SER


    UNION ALL

    SELECT TO_CHAR(RM.RT_BILL_DATE,'YYYY-MM') AS PERIOD,
           0 AS NET_SALES,
           (NVL(RD.I_PRICE,0)-NVL(RD.DIS_AMT,0)+NVL(RD.OTHR_AMT,0))
           * NVL(RD.I_QTY,0)
           * NVL(RM.RT_BILL_RATE,1) AS RT_NET_SALES
    FROM IAS202538.IAS_RT_BILL_MST RM
    JOIN IAS202538.IAS_RT_BILL_DTL RD
    ON RM.RT_BILL_DOC_TYPE = RD.RT_BILL_DOC_TYPE
    AND RM.RT_BILL_NO       = RD.RT_BILL_NO
    AND RM.RT_BILL_SER      = RD.RT_BILL_SER

)
GROUP BY PERIOD
ORDER BY PERIOD;

=======================================================================


=======================================================================
[FINAL OUTPUT RULE]

Respond with ONLY the SQL query that answers the user's question.
NO explanations.
NO comments.
NO markdown.
=======================================================================
"""


    
    def _create_user_prompt(self, query: str, schema_context: str, conversation_context: str, 
                           validation_error: Optional[str] = None) -> str:
        """Create user prompt with query and context"""
        prompt = f"""Schema Context (specify this schema IAS202538):
{schema_context}

User Query: {query}
"""
        
        if validation_error:
            prompt += f"\n⚠️ VALIDATION ERROR FROM PREVIOUS ATTEMPT:\n{validation_error}\n\n"
        
        prompt += """Generate a SQL query that answers this question. Consider:
- Generate syntactically correct Oracle Database 12c SQL
- Use ONLY columns that are explicitly listed in the schema above

SQL Query:"""
        return prompt
    
    def generate_sql(self, query: str, conversation_id: str = "default") -> Tuple[str, List[str]]:
        """Generate SQL query from natural language with validation retry"""
        relevant_tables = self.schema_processor.filter_relevant_tables(query)
        
        with open("relevant_tables.txt", "w", encoding="utf-8") as f:
            f.write("Relevant tables retrieved:\n")
            f.write(str(relevant_tables))
        if not relevant_tables:
            return "-- No relevant tables found for the query", [], None
        
        schema_context = self._create_schema_context(relevant_tables)
        conversation_context = self._format_conversation_context(
            self.memory_manager.get_conversation_context(conversation_id)
        )
        
        validation_error = None
        
        # Retry loop for validation
        for attempt in range(self.max_retries):
            try:
                logger.info(f"Generating SQL - Attempt {attempt + 1}/{self.max_retries}")
                
                messages = [
                    {"role": "system", "content": self._create_system_prompt()},
                    {"role": "user", "content": self._create_user_prompt(
                        query, schema_context, conversation_context, validation_error
                    )}
                ]
                
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    extra_body={
                        "top_k": 20, 
                        "repetition_penalty": 1.05
                    },
                    temperature=0.2,
                    top_p=0.8
                )
                
                print("messages\n", messages)
                sql_query = response.choices[0].message.content.strip()
                sql_query = self._clean_sql_query(sql_query)
                
                # Validate the generated SQL
                is_valid, invalid_columns = self.validator.validate_columns(sql_query, relevant_tables)
                
                print("sql query", sql_query, type(sql_query))
                
                if "IAS202538" not in sql_query:
                    sql_query = self.add_schema_prefix(sql_query)
                
                if is_valid:
                    logger.info("✓ SQL validation passed")
                    
                    # Store in memory
                    self.memory_manager.add_message(conversation_id, "user", query)
                    self.memory_manager.add_message(conversation_id, "assistant", 
                                                   f"Generated SQL query", sql_query)
                    
                    # Update used tables
                    conversation = self.memory_manager.get_or_create_conversation(conversation_id)
                    table_names = [table.name for table in relevant_tables]
                    conversation.used_tables.extend(table_names)
                    conversation.used_tables = list(set(conversation.used_tables))
                    
                    return sql_query, table_names, schema_context
                else:
                    logger.warning(f"✗ SQL validation failed: {invalid_columns}")
                    validation_error = self.validator.format_validation_error(
                        invalid_columns, relevant_tables
                    )
                    
                    if attempt == self.max_retries - 1:
                        error_msg = f"{sql_query}"
                        return error_msg, [], None
                    
            except Exception as e:
                logger.error(f"Error generating SQL on attempt {attempt + 1}: {e}")
                if attempt == self.max_retries - 1:
                    return f"-- Error generating SQL: {str(e)}", [], None
        
        return "", [], None
    
    def _clean_sql_query(self, sql_query: str) -> str:
        """Clean and format the SQL query"""
        sql_query = re.sub(r'```sql\n?', '', sql_query)
        sql_query = re.sub(r'```\n?', '', sql_query)
        sql_query = re.sub(r'\n\s*\n', '\n', sql_query.strip())
        return sql_query

    def _format_conversation_context(self, messages: List[Dict]) -> str:
        """Format conversation context for prompt"""
        if not messages:
            return "No previous conversation."
        
        context = "Recent conversation:\n"
        for msg in messages[-5:]:
            role = "User" if msg['role'] == 'user' else "Assistant"
            context += f"{role}: {msg['content']}\n"
            if 'sql_query' in msg:
                context += f"SQL: {msg['sql_query'][:100]}...\n"
        
        return context
    
    def explain_query(self, sql_query: str, conversation_id: str = "default") -> str:
        """Explain what a SQL query does"""
        try:
            messages = [
                {"role": "system", "content": "You are an SQL expert. Explain SQL queries in simple business terms."},
                {"role": "user", "content": f"Explain this SQL query in business terms:\n\n{sql_query}"}
            ]
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.3
            )
            
            explanation = response.choices[0].message.content.strip()
            
            self.memory_manager.add_message(conversation_id, "user", f"Explain query: {sql_query}")
            self.memory_manager.add_message(conversation_id, "assistant", explanation)
            
            return explanation
            
        except Exception as e:
            logger.error(f"Error explaining query: {e}")
            return f"Error explaining query: {str(e)}"
    
    def get_conversation_history(self, conversation_id: str = "default") -> List[Dict]:
        """Get conversation history"""
        return self.memory_manager.get_conversation_context(conversation_id, max_messages=9)
    
    def clear_conversation(self, conversation_id: str = "default"):
        """Clear conversation history"""
        if conversation_id in self.memory_manager.conversations:
            del self.memory_manager.conversations[conversation_id]
            self.memory_manager.save_memory()

    async def generate(self, prompt: str) -> str:
        """Generate text based on a prompt using the LLM"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a professional Arabic-to-English translator for an ERP text-to-SQL system. "
                            "Translate Arabic business terms accurately for SQL intent. "
                            "Mapping rules: "
                            "'مردود'/'مردودات'/'مرتجع'/'مرتجعات'/'ترجيع' => 'returns', "
                            "'مبيعات' => 'sales', "
                            "'صافي'/'صافى' => 'net sales', "
                            "'إجمالي' => 'total'. "
                            "Return ONLY the English translation. Do not add explanations."
                        )
                    },
                    {
                        "role": "user", 
                        "content": prompt
                    }
                ],
                temperature=0.3
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Error generating text: {e}")
            return f"Error generating text: {str(e)}"

    def add_schema_prefix(self, sql_query, schema_name='IAS202538'):
        """
        Adds schema prefix to table names in SQL queries for Oracle.
        """
        result = sql_query
        print("adding schema prefix")
        print(result)
        print(sql_query)
        
        # Handle FROM clause
        pattern = r'(\bFROM\s+)([A-Za-z_][A-Za-z0-9_$#]*)\b'
        result = re.sub(pattern, 
                        lambda m: f'{m.group(1)}{schema_name}.{m.group(2)}' 
                        if f'{schema_name}.' not in m.group(0) else m.group(0),
                        result, flags=re.IGNORECASE)
        
        # Handle all JOIN types
        pattern = r'(\bJOIN\s+)([A-Za-z_][A-Za-z0-9_$#]*)\b'
        result = re.sub(pattern, 
                        lambda m: f'{m.group(1)}{schema_name}.{m.group(2)}' 
                        if f'{schema_name}.' not in m.group(0) else m.group(0),
                        result, flags=re.IGNORECASE)
        
        # Handle UPDATE clause
        pattern = r'(\bUPDATE\s+)([A-Za-z_][A-Za-z0-9_$#]*)\b'
        result = re.sub(pattern, 
                        lambda m: f'{m.group(1)}{schema_name}.{m.group(2)}' 
                        if f'{schema_name}.' not in m.group(0) else m.group(0),
                        result, flags=re.IGNORECASE)
        
        # Handle INSERT INTO clause
        pattern = r'(\bINTO\s+)([A-Za-z_][A-Za-z0-9_$#]*)\b'
        result = re.sub(pattern, 
                        lambda m: f'{m.group(1)}{schema_name}.{m.group(2)}' 
                        if f'{schema_name}.' not in m.group(0) else m.group(0),
                        result, flags=re.IGNORECASE)
        
        # Handle DELETE FROM clause
        pattern = r'(\bDELETE\s+FROM\s+)([A-Za-z_][A-Za-z0-9_$#]*)\b'
        result = re.sub(pattern, 
                        lambda m: f'{m.group(1)}{schema_name}.{m.group(2)}' 
                        if f'{schema_name}.' not in m.group(0) else m.group(0),
                        result, flags=re.IGNORECASE)
        
        # Handle MERGE INTO clause
        pattern = r'(\bMERGE\s+INTO\s+)([A-Za-z_][A-Za-z0-9_$#]*)\b'
        result = re.sub(pattern, 
                        lambda m: f'{m.group(1)}{schema_name}.{m.group(2)}' 
                        if f'{schema_name}.' not in m.group(0) else m.group(0),
                        result, flags=re.IGNORECASE)
        
        # Handle USING clause
        pattern = r'(\bUSING\s+)([A-Za-z_][A-Za-z0-9_$#]*)\b'
        result = re.sub(pattern, 
                        lambda m: f'{m.group(1)}{schema_name}.{m.group(2)}' 
                        if f'{schema_name}.' not in m.group(0) else m.group(0),
                        result, flags=re.IGNORECASE)
        
        # Handle comma-separated tables
        pattern = r'(,\s*)([A-Za-z_][A-Za-z0-9_$#]*)(\s+)'
        result = re.sub(pattern, 
                        lambda m: f'{m.group(1)}{schema_name}.{m.group(2)}{m.group(3)}' 
                        if f'{schema_name}.' not in m.group(0) else m.group(0),
                        result, flags=re.IGNORECASE)
        
        return result

# Example usage
if __name__ == "__main__":
    # Initialize the generator
    generator = TextToSQLGenerator(api_key="your-api-key")
    
    # Generate SQL with validation
    query = "Show me all customers with their total sales amount"
    sql_query, tables = generator.generate_sql(query)
    
    print("Generated SQL:")
    print(sql_query)
    print(f"\nTables used: {tables}")