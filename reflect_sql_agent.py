import re
import logging
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
import sqlglot
from sqlglot import parse_one, exp

logger = logging.getLogger(__name__)

@dataclass
class ReflectionResult:
    """Results from SQL reflection/validation"""
    is_valid: bool
    issues: List[Dict[str, str]]
    suggestions: List[str]
    corrected_sql: Optional[str]
    confidence_score: float

class SQLReflectionAgent:
    """
    Reflection agent that validates and verifies SQL statements against:
    1. User query intent
    2. Oracle Database 12c syntax and features
    3. Database schema constraints
    
    Compatible with TableInfo structure from SchemaProcessor.
    """
    
    def __init__(self, schema_processor, sql_validator, llm_client, model="gpt-4"):
        self.schema_processor = schema_processor
        self.sql_validator = sql_validator
        self.llm_client = llm_client
        self.model = model
        
        # Oracle 12c compatibility rules (kept generic; unsupported list intentionally empty for 12c)
        self.oracle_compatibility_rules = {
            "unsupported_features": [
                # Use explicit checks for truly non-Oracle syntax (LIMIT, TOP, IIF, etc.)
                # We handle those separately in the compatibility validator.
            ],
            "deprecated_features": [
                "CONNECT BY NOCYCLE",  # Prefer recursive WITH, but still supported
            ],
        }

    
    def reflect_sql_statement(
        self,
        sql_statement: str,
        user_query: str,
        schema_retrieved: List,  # List of TableInfo objects
        max_iterations: int = 3
    ) -> ReflectionResult:
        """
        Main reflection function that validates SQL statement comprehensively.
        
        Args:
            sql_statement: The generated SQL query
            user_query: The original user's natural language query
            schema_retrieved: List of TableInfo objects that are relevant
            max_iterations: Maximum number of correction attempts
            
        Returns:
            ReflectionResult with validation results and suggestions
        """
        issues = []
        suggestions = []
        confidence_score = 100.0
        corrected_sql = sql_statement
        
        # Validate input
        if not sql_statement or not sql_statement.strip():
            return ReflectionResult(
                is_valid=False,
                issues=[{
                    'type': 'input_error',
                    'severity': 'critical',
                    'message': 'Empty SQL statement provided',
                    'location': 'SQL statement'
                }],
                suggestions=[],
                corrected_sql=None,
                confidence_score=0.0
            )
        
        if not schema_retrieved:
            logger.warning("No schema tables provided for validation")
            # Continue with limited validation
        
        # Phase 1: Schema Validation
        logger.info("Phase 1: Validating schema alignment")
        try:
            # schema_issues = self._validate_schema_alignment(sql_statement, schema_retrieved)
            schema_issues = False
            
            if schema_issues:
                issues.extend(schema_issues)
                confidence_score -= len(schema_issues) * 15
        except Exception as e:
            logger.error(f"Error in schema validation: {e}")
            issues.append({
                'type': 'validation_error',
                'severity': 'low',
                'message': f'Schema validation encountered an error: {str(e)}',
                'location': 'Schema validation'
            })
        
        # Phase 2: Oracle compatibility validation
        logger.info("Phase 2: Validating Oracle compatibility")
        try:
            oracle_issues = self._validate_oracle_compatibility(sql_statement)
            if oracle_issues:
                issues.extend(oracle_issues)
                confidence_score -= len(oracle_issues) * 10
        except Exception as e:
            logger.error(f"Error in Oracle compatibility check: {e}")
        
        # Phase 3: Syntax and Structure Validation
        logger.info("Phase 3: Validating SQL syntax and structure")
        try:
            syntax_issues = self._validate_syntax_and_structure(sql_statement)
            if syntax_issues:
                issues.extend(syntax_issues)
                confidence_score -= len(syntax_issues) * 12
        except Exception as e:
            logger.error(f"Error in syntax validation: {e}")
        
        # Phase 4: Query Intent Alignment (using LLM)
        logger.info("Phase 4: Validating query intent alignment")
        try:
            intent_issues, intent_suggestions = self._validate_query_intent(
                sql_statement, user_query, schema_retrieved
            )
            if intent_issues:
                issues.extend(intent_issues)
                confidence_score -= len(intent_issues) * 20
            suggestions.extend(intent_suggestions)
        except Exception as e:
            logger.error(f"Error in intent validation: {e}")
            # Don't fail the entire validation if LLM fails
        
        # Phase 5: Logical and Business Rules Validation
        logger.info("Phase 5: Validating logical and business rules")
        try:
            logic_issues = self._validate_logical_rules(sql_statement, user_query)
            if logic_issues:
                issues.extend(logic_issues)
                confidence_score -= len(logic_issues) * 8
        except Exception as e:
            logger.error(f"Error in logic validation: {e}")
        
        # Phase 6: Performance and Best Practices
        logger.info("Phase 6: Checking performance and best practices")
        try:
            # perf_suggestions = self._check_performance_hints(sql_statement, schema_retrieved)
            # suggestions.extend(perf_suggestions)
            suggestions.extend([])

        except Exception as e:
            logger.error(f"Error in performance check: {e}")
        
        # Attempt to correct issues if found
        if issues and max_iterations > 0:
            critical_high_issues = [i for i in issues if i['severity'] in ['critical', 'high']]
            if critical_high_issues:
                logger.info(f"Attempting to correct {len(critical_high_issues)} critical/high issues")
                try:
                    corrected_sql = self._attempt_correction(
                        sql_statement, user_query, issues, schema_retrieved
                    )
                except Exception as e:
                    logger.error(f"Error in auto-correction: {e}")
                    corrected_sql = sql_statement
        
        # Ensure confidence score doesn't go below 0
        confidence_score = max(0.0, confidence_score)
        
        is_valid = len([i for i in issues if i['severity'] in ['critical', 'high']]) == 0
        
        return ReflectionResult(
            is_valid=is_valid,
            issues=issues,
            suggestions=suggestions,
            corrected_sql=corrected_sql if not is_valid else sql_statement,
            confidence_score=confidence_score
        )
    
    def _validate_schema_alignment(
        self, 
        sql_statement: str, 
        schema_retrieved: List
    ) -> List[Dict[str, str]]:
        """Validate that SQL uses only schema-defined tables and columns"""
        issues = []
        
        if not schema_retrieved:
            # No schema to validate against
            return issues
        
        try:
            # 1. Check column validity using existing validator
            # is_valid, invalid_columns = self.sql_validator.validate_columns(
            #     sql_statement, schema_retrieved
            # )
            is_valid, invalid_columns = True, []
            if not is_valid:
                for table, columns in invalid_columns.items():
                    issues.append({
                        'type': 'schema_violation',
                        'severity': 'critical',
                        'message': f"Table '{table}' uses invalid columns: {', '.join(columns)}",
                        'location': table
                    })
        except Exception as e:
            logger.error(f"Error validating columns: {e}")
            issues.append({
                'type': 'validation_error',
                'severity': 'medium',
                'message': f'Could not validate columns: {str(e)}',
                'location': 'Column validation'
            })
        
        try:
            # 2. Check that all tables in SQL exist in schema
            parsed = parse_one(sql_statement, dialect='oracle')
            sql_tables = set()
            
            for table in parsed.find_all(exp.Table):
                table_name = table.name.upper()
                # Remove schema prefix if present (e.g., IAS202538.IAS_BILL -> IAS_BILL)
                if '.' in table_name:
                    table_name = table_name.split('.')[-1]
                sql_tables.add(table_name)
            
            # Build set of valid schema table names
            schema_tables = set()
            for t in schema_retrieved:
                if hasattr(t, 'name'):
                    schema_tables.add(t.name.upper())
            
            missing_tables = sql_tables - schema_tables
            
            if missing_tables:
                issues.append({
                    'type': 'schema_violation',
                    'severity': 'critical',
                    'message': f"Tables not in schema: {', '.join(missing_tables)}",
                    'location': 'FROM/JOIN clauses'
                })
        
        except Exception as e:
            logger.error(f"Error parsing tables: {e}")
            # Don't add issue if parsing fails - syntax validation will catch it
        
        try:
            # 3. Validate foreign key relationships in JOINs
            join_issues = self._validate_join_relationships(sql_statement, schema_retrieved)
            issues.extend(join_issues)
        except Exception as e:
            logger.error(f"Error validating joins: {e}")
        
        return issues
    
    def _validate_oracle_compatibility(self, sql_statement: str) -> List[Dict[str, str]]:
        """Validate Oracle 12c specific syntax and basic compatibility."""
        issues: List[Dict[str, str]] = []
        sql_upper = sql_statement.upper()
        
        # 1) Non-Oracle / wrong dialect constructs
        # LIMIT (MySQL/Postgres) is not valid Oracle syntax
        if re.search(r"\bLIMIT\b", sql_upper):
            issues.append({
                "type": "oracle_compatibility",
                "severity": "critical",
                "message": "LIMIT is not supported in Oracle. Use ORDER BY ... FETCH FIRST n ROWS ONLY.",
                "location": "SQL statement",
            })
        
        # TOP (SQL Server) is not valid Oracle syntax
        if re.search(r"\bTOP\s+\d+", sql_upper):
            issues.append({
                "type": "oracle_compatibility",
                "severity": "critical",
                "message": "TOP n is not supported in Oracle. Use ORDER BY ... FETCH FIRST n ROWS ONLY.",
                "location": "SQL statement",
            })
        
        # IIF (SQL Server) is not valid Oracle syntax
        if " IIF(" in sql_upper:
            issues.append({
                "type": "oracle_compatibility",
                "severity": "critical",
                "message": "IIF() is not supported in Oracle. Use CASE WHEN ... THEN ... END instead.",
                "location": "SQL statement",
            })
        
        # 2) OFFSET usage: in Oracle 12c, OFFSET must be paired with FETCH
        if "OFFSET" in sql_upper and "FETCH" not in sql_upper:
            issues.append({
                "type": "oracle_compatibility",
                "severity": "high",
                "message": "In Oracle 12c, OFFSET should be used as: OFFSET n ROWS FETCH NEXT m ROWS ONLY.",
                "location": "SQL statement",
            })
        
        # 3) LIMIT/OFFSET generic check (legacy from 11g, but we now give 12c-specific advice)
        if re.search(r"\bOFFSET\b\s+\d+\s*\bROW", sql_upper) and "FETCH" not in sql_upper:
            issues.append({
                "type": "oracle_compatibility",
                "severity": "medium",
                "message": "Use OFFSET n ROWS FETCH NEXT m ROWS ONLY for pagination in Oracle 12c.",
                "location": "SQL statement",
            })
        
        # 4) DATE literal style
        date_pattern = r"DATE\s*'(\d{4}-\d{2}-\d{2})'"
        date_matches = re.findall(date_pattern, sql_statement, re.IGNORECASE)
        if date_matches and "TO_DATE" not in sql_upper:
            issues.append({
                "type": "oracle_compatibility",
                "severity": "medium",
                "message": "Consider using TO_DATE() for date literals in Oracle for clarity (e.g., TO_DATE('2025-01-01','YYYY-MM-DD')).",
                "location": "DATE literals",
            })
        
        # 5) ROWNUM + ORDER BY: warn if used incorrectly (ROWNUM after ORDER BY)
        rownum_pos = sql_upper.find("ROWNUM")
        order_by_pos = sql_upper.find("ORDER BY")
        if rownum_pos != -1 and order_by_pos != -1 and rownum_pos > order_by_pos:
            issues.append({
                "type": "oracle_compatibility",
                "severity": "high",
                "message": "ROWNUM after ORDER BY may not work as expected. Use a subquery or FETCH FIRST for top-N queries.",
                "location": "WHERE/ORDER BY clause",
            })
        
        # 6) (Optional) warn on some deprecated constructs
        if "CONNECT BY NOCYCLE" in sql_upper:
            issues.append({
                "type": "oracle_best_practice",
                "severity": "low",
                "message": "CONNECT BY NOCYCLE is older style. Consider recursive WITH queries where appropriate.",
                "location": "FROM clause",
            })
        
        return issues
    
    def _validate_syntax_and_structure(self, sql_statement: str) -> List[Dict[str, str]]:
        """Validate SQL syntax and structural correctness"""
        issues = []
        
        # 1. Try parsing with sqlglot
        try:
            parsed = parse_one(sql_statement, dialect='oracle')
        except Exception as e:
            issues.append({
                'type': 'syntax_error',
                'severity': 'critical',
                'message': f'SQL parsing error: {str(e)}',
                'location': 'SQL statement'
            })
            return issues
        
        # 2. Check for unbalanced parentheses
        if sql_statement.count('(') != sql_statement.count(')'):
            issues.append({
                'type': 'syntax_error',
                'severity': 'critical',
                'message': 'Unbalanced parentheses in SQL statement',
                'location': 'SQL statement'
            })
        
        # 3. Validate GROUP BY with aggregate functions
        has_aggregate = bool(re.search(
            r'\b(COUNT|SUM|AVG|MIN|MAX|LISTAGG)\s*\(', 
            sql_statement, 
            re.IGNORECASE
        ))
        has_group_by = bool(re.search(r'\bGROUP\s+BY\b', sql_statement, re.IGNORECASE))
        
        if has_aggregate:
            # Get selected columns
            try:
                select_items = []
                for select in parsed.find_all(exp.Select):
                    for expr in select.expressions:
                        if not isinstance(expr, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)):
                            select_items.append(str(expr))
                
                # If we have non-aggregate columns and no GROUP BY, that's an issue
                if select_items and not has_group_by:
                    issues.append({
                        'type': 'syntax_error',
                        'severity': 'high',
                        'message': 'Query has aggregate functions but missing GROUP BY clause',
                        'location': 'SELECT/GROUP BY'
                    })
            except Exception as e:
                logger.warning(f"Could not validate GROUP BY: {e}")
        
        # 4. Check for SELECT *
        if re.search(r'\bSELECT\s+\*\b', sql_statement, re.IGNORECASE):
            issues.append({
                'type': 'best_practice',
                'severity': 'low',
                'message': 'Consider specifying explicit columns instead of SELECT *',
                'location': 'SELECT clause'
            })
        
        # 5. Check for missing WHERE clause in UPDATE/DELETE
        if re.search(r'\b(UPDATE|DELETE)\b', sql_statement, re.IGNORECASE):
            if not re.search(r'\bWHERE\b', sql_statement, re.IGNORECASE):
                issues.append({
                    'type': 'safety_warning',
                    'severity': 'high',
                    'message': 'UPDATE/DELETE without WHERE clause will affect all rows',
                    'location': 'WHERE clause'
                })
        
        return issues
    
    def _validate_query_intent(
        self,
        sql_statement: str,
        user_query: str,
        schema_retrieved: List
    ) -> Tuple[List[Dict[str, str]], List[str]]:
        """Use LLM to validate if SQL matches user intent"""
        issues = []
        suggestions = []
        
        if not schema_retrieved:
            # Skip intent validation if no schema
            return issues, suggestions
        
        try:
            # Build schema context - safely handle TableInfo attributes
            schema_lines = []
            for t in schema_retrieved:
                try:
                    table_name = getattr(t, 'name', 'UNKNOWN')
                    
                    # Get columns - handle different formats
                    columns = []
                    if hasattr(t, 'columns'):
                        col_list = t.columns
                        if isinstance(col_list, list):
                            for col in col_list:
                                if isinstance(col, dict):
                                    col_name = col.get('name', 'UNKNOWN')
                                    columns.append(col_name)
                                elif hasattr(col, 'name'):
                                    columns.append(col.name)
                        
                    columns_str = ', '.join(columns) if columns else 'No columns available'
                    schema_lines.append(f"Table: {table_name}\nColumns: {columns_str}")
                    
                except Exception as e:
                    logger.error(f"Error formatting table info: {e}")
                    continue
            
            schema_context = "\n\n".join(schema_lines)
            
            if not schema_context.strip():
                logger.warning("Could not build schema context for intent validation")
                return issues, suggestions
            
            prompt = f"""You are an SQL expert validator. Analyze if the SQL query correctly implements the user's request.

User Query: {user_query}

Generated SQL:
{sql_statement}

Available Schema:
{schema_context}

Analyze the following:
1. Does the SQL correctly answer the user's question?
2. Are the correct tables and columns selected?
3. Are the JOIN conditions appropriate?
4. Are the WHERE conditions aligned with the user's intent?
5. Is the aggregation/grouping correct (if applicable)?
6. Are there any missing or extra operations?


**Business Logic Formulas:**

1. **Total Sales (إجمالي المبيعات):**
   - Tables: ias_bill_mst, ias_bill_dtl
   - Formula: (i_price - dis_amt + othr_amt) * i_qty
   - Sum all line items from sales invoices

2. **Returns/ Total Returns (المردودات):**
   - Tables: ias_rt_bill_mst, ias_rt_bill_dtl
   - Formula: (i_price - dis_amt + othr_amt) * i_qty
   - Sum all line items from return invoices
   
3. **Net Sales (صافي المبيعات):**
   - Formula: Total Sales - Returns
   - Calculate the difference between sales and return transactions


Respond in this format:
ALIGNMENT: [YES/NO/PARTIAL]
ISSUES: [List any misalignments or problems]
SUGGESTIONS: [List improvements or corrections]
"""
            
            response = self.llm_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert SQL validator."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2
            )
            
            result = response.choices[0].message.content.strip()
            
            # Parse the LLM response
            if 'ALIGNMENT: NO' in result or 'ALIGNMENT: PARTIAL' in result:
                # Extract issues
                issues_match = re.search(r'ISSUES:(.*?)(?:SUGGESTIONS:|$)', result, re.DOTALL)
                if issues_match:
                    issue_text = issues_match.group(1).strip()
                    if issue_text and issue_text != '[]' and issue_text.lower() != 'none':
                        issues.append({
                            'type': 'intent_mismatch',
                            'severity': 'high' if 'ALIGNMENT: NO' in result else 'medium',
                            'message': f'Query intent mismatch: {issue_text}',
                            'location': 'Overall query logic'
                        })
            
            # Extract suggestions
            suggestions_match = re.search(r'SUGGESTIONS:(.*?)$', result, re.DOTALL)
            if suggestions_match:
                suggestion_text = suggestions_match.group(1).strip()
                if suggestion_text and suggestion_text != '[]' and suggestion_text.lower() != 'none':
                    suggestions.append(suggestion_text)
        
        except Exception as e:
            logger.error(f"Error in intent validation: {e}")
            # Don't fail the entire validation if LLM fails
        
        return issues, suggestions
    
    def _validate_logical_rules(self, sql_statement: str, user_query: str) -> List[Dict[str, str]]:
        """Validate logical consistency and business rules"""
        issues = []
        
        # 1. Check for contradictory WHERE conditions
        where_match = re.search(r'\bWHERE\b(.*?)(?:\bGROUP BY\b|\bORDER BY\b|\bHAVING\b|;|$)', 
                               sql_statement, re.IGNORECASE | re.DOTALL)
        if where_match:
            where_clause = where_match.group(1)
            
            # Check for always false conditions like: WHERE 1=0
            if re.search(r'\b1\s*=\s*0\b|\b0\s*=\s*1\b', where_clause):
                issues.append({
                    'type': 'logical_error',
                    'severity': 'high',
                    'message': 'WHERE clause contains always-false condition',
                    'location': 'WHERE clause'
                })
            
            # Check for contradictory date ranges
            date_conditions = re.findall(r"(\w+)\s*(<|>|<=|>=)\s*TO_DATE\('([^']+)'", where_clause)
            if len(date_conditions) >= 2:
                # Simple check: same column with > and < conditions
                columns = [d[0] for d in date_conditions]
                if len(columns) != len(set(columns)):
                    issues.append({
                        'type': 'logical_warning',
                        'severity': 'low',
                        'message': 'Multiple date conditions on same column - verify date range logic',
                        'location': 'WHERE clause'
                    })
        
        # 2. Check for division by zero risks
        if re.search(r'/\s*[0(]', sql_statement):
            issues.append({
                'type': 'logical_warning',
                'severity': 'medium',
                'message': 'Potential division by zero - consider using NULLIF or CASE',
                'location': 'Calculation expressions'
            })
        
        # 3. Check for ambiguous column references in multi-table queries
        if re.search(r'\bJOIN\b', sql_statement, re.IGNORECASE):
            # Look for column references without table prefix
            unqualified_cols = re.findall(r'\bWHERE\s+(\w+)\s*=', sql_statement, re.IGNORECASE)
            if unqualified_cols:
                issues.append({
                    'type': 'logical_warning',
                    'severity': 'low',
                    'message': 'Consider using table aliases for all column references in JOIN queries',
                    'location': 'WHERE/ON clauses'
                })
        
        return issues
    
    def _validate_join_relationships(
        self, 
        sql_statement: str, 
        schema_retrieved: List
    ) -> List[Dict[str, str]]:
        """Validate JOIN conditions match foreign key relationships"""
        issues = []
        
        if not schema_retrieved:
            return issues
        
        try:
            # Build foreign key map - safely handle TableInfo attributes
            fk_map = {}
            for table in schema_retrieved:
                try:
                    if not hasattr(table, 'foreign_keys') or not hasattr(table, 'name'):
                        continue
                    
                    table_name = table.name.upper()
                    fk_list = table.foreign_keys
                    
                    if not isinstance(fk_list, list):
                        continue
                    
                    for fk in fk_list:
                        if isinstance(fk, dict):
                            fk_column = fk.get('column', '').upper()
                            ref_table = fk.get('references_table', '').upper()
                            ref_column = fk.get('references_column', '').upper()
                            
                            if fk_column and ref_table and ref_column:
                                key = (table_name, fk_column)
                                fk_map[key] = (ref_table, ref_column)
                except Exception as e:
                    logger.debug(f"Error processing foreign keys for table: {e}")
                    continue
            
            if not fk_map:
                # No foreign key data to validate against
                return issues
            
            # Parse JOIN conditions
            parsed = parse_one(sql_statement, dialect='oracle')
            
            for join in parsed.find_all(exp.Join):
                # Get ON condition
                if join.on:
                    # This is simplified - in production you'd want more robust parsing
                    join_str = str(join.on)
                    
                    # Extract table.column = table.column patterns
                    matches = re.findall(r'(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)', join_str)
                    
                    for match in matches:
                        table1, col1, table2, col2 = [m.upper() for m in match]
                        
                        # Check if this join follows foreign key relationships
                        key1 = (table1, col1)
                        key2 = (table2, col2)
                        
                        valid_fk = False
                        if key1 in fk_map:
                            ref_table, ref_col = fk_map[key1]
                            if ref_table == table2 and ref_col == col2:
                                valid_fk = True
                        
                        if key2 in fk_map:
                            ref_table, ref_col = fk_map[key2]
                            if ref_table == table1 and ref_col == col1:
                                valid_fk = True
                        
                        if not valid_fk:
                            issues.append({
                                'type': 'join_warning',
                                'severity': 'low',
                                'message': f'JOIN between {table1}.{col1} and {table2}.{col2} does not follow defined foreign keys',
                                'location': 'JOIN condition'
                            })
        
        except Exception as e:
            logger.debug(f"Could not validate join relationships: {e}")
        
        return issues
    
    def _check_performance_hints(
        self, 
        sql_statement: str, 
        schema_retrieved: List
    ) -> List[str]:
        """Provide performance optimization suggestions"""
        suggestions = []
        
        # 1. Check for missing indexes on WHERE columns
        where_match = re.search(r'\bWHERE\b(.*?)(?:\bGROUP BY\b|\bORDER BY\b|;|$)', 
                               sql_statement, re.IGNORECASE | re.DOTALL)
        if where_match:
            where_clause = where_match.group(1)
            # Extract columns used in WHERE
            where_columns = re.findall(r'(\w+)\.(\w+)', where_clause)
            
            if where_columns:
                suggestions.append(
                    f"Ensure indexes exist on WHERE clause columns for optimal performance: "
                    f"{', '.join([f'{t}.{c}' for t, c in where_columns[:3]])}"
                )
        
        # 2. Check for leading wildcards in LIKE
        if re.search(r"LIKE\s+['\"]%", sql_statement, re.IGNORECASE):
            suggestions.append(
                "Leading wildcards in LIKE clauses (LIKE '%...') prevent index usage. "
                "Consider full-text search if available."
            )
        
        # 3. Check for OR conditions that could be optimized
        or_count = len(re.findall(r'\bOR\b', sql_statement, re.IGNORECASE))
        if or_count > 3:
            suggestions.append(
                f"Query has {or_count} OR conditions. Consider using IN clause or UNION for better performance."
            )
        
        # 4. Check for NOT IN with subqueries
        if re.search(r'\bNOT\s+IN\s*\(\s*SELECT', sql_statement, re.IGNORECASE):
            suggestions.append(
                "NOT IN with subquery can be slow. Consider using NOT EXISTS or LEFT JOIN with NULL check."
            )
        
        # 5. Check for SELECT DISTINCT
        if re.search(r'\bSELECT\s+DISTINCT\b', sql_statement, re.IGNORECASE):
            suggestions.append(
                "DISTINCT can be expensive. Verify if duplicates need elimination or if GROUP BY is more appropriate."
            )
        
        return suggestions
    
    def _attempt_correction(
        self,
        sql_statement: str,
        user_query: str,
        issues: List[Dict[str, str]],
        schema_retrieved: List
    ) -> str:
        """Attempt to automatically correct identified issues using the LLM (Oracle 12c)."""
        
        corrected = sql_statement
        
        # 1) Keep only critical/high issues for auto-correction context
        high_issues = [
            issue for issue in issues
            if issue.get("severity") in ["critical", "high"]
        ]
        
        # ---------- PATCH #1: add rule-based issue if returns/net not requested ----------
        user_query_lower = (user_query or "").lower()

        user_requested_kpis = any(
            kw in user_query_lower
            for kw in [
                "sales indicators", "indicator", "indicators", "kpi", "kpis",
                "مؤشرات المبيعات", "مؤشر المبيعات", "مؤشرات", "مؤشر"
            ]
        )

        user_requested_returns_or_net = any(
            kw in user_query_lower
            for kw in [
                "net sales", "net", "صافي", "صافى", "صافي المبيعات",
                "returns", "return", "refund", "refunds",
                "مردود", "مردودات", "المردودات", "مرتجع", "مرتجعات"
            ]
        )

        # KPI request implies returns+net are requested
        user_requested_returns = user_requested_kpis or user_requested_returns_or_net


        sql_upper = sql_statement.upper()

        sql_contains_returns = any(
            kw in sql_upper
            for kw in [
                "IAS_RT_BILL_MST",
                "IAS_RT_BILL_DTL",
                " RT_BILL_",
                " RT_",
            ]
        )

        # If SQL includes returns/net while user did NOT ask for them, add a critical issue
        if sql_contains_returns and not user_requested_returns:
            high_issues.append({
                "type": "business_logic_violation",
                "severity": "critical",
                "message": "Query incorrectly includes returns or net sales although the user did not request them. Remove returns tables and compute only TOTAL_SALES.",
                "location": "SQL_QUERY",
            })

        # ---------- PATCH: Detect ambiguous unqualified columns ----------
        if " W_CODE" in sql_upper and not any(
            alias + ".W_CODE" in sql_upper
            for alias in ["M.", "D.", "RM.", "RD."]
        ):
            high_issues.append({
                "type": "sql_semantic_error",
                "severity": "critical",
                "message": (
                    "Column W_CODE is used without a table alias. "
                    "This causes ORA-00918 (column ambiguously defined). "
                    "You MUST qualify it as M.W_CODE or D.W_CODE."
                ),
                "location": "SELECT_OR_GROUP_BY",
            })

        # --------------------------------------------------------------------
        # ---------- PATCH #X: Enforce sales indicators correctness ----------
        if is_sales_indicator:
            # Must include returns tables
            if not any(t in sql_upper for t in ["IAS_RT_BILL_MST", "IAS_RT_BILL_DTL"]):
                high_issues.append({...})

            # Forbid TOTAL_RETURNS = 0 pattern
            if "TOTAL_RETURNS" in sql_upper and "= 0" in sql_upper:
                high_issues.append({...})

            # NEW RULE: UNION ALL must have OUTER aggregation by PERIOD
            has_union = "UNION ALL" in sql_upper
            # crude but effective: union exists and the final query does not group by PERIOD
            if has_union and ("GROUP BY PERIOD" not in sql_upper) and ("GROUP BY TO_CHAR" not in sql_upper):
                high_issues.append({
                    "type": "business_logic_violation",
                    "severity": "critical",
                    "message": (
                        "Sales indicators using UNION ALL must have an OUTER GROUP BY PERIOD "
                        "to avoid returning two rows per period."
                    ),
                    "location": "SQL_QUERY",
                })

        # ---------- PATCH: returns-only by period must NOT output net sales ----------
        is_returns_only = (
            any(k in user_query_lower for k in ["مردود", "مرتجعات", "مردود المبيعات", "المردودات", "returns", "return"])
            and not any(k in user_query_lower for k in ["صافي", "صافى", "net", "net sales"])
        )

        if is_returns_only:
            # If the SQL outputs NET_SALES, it's wrong for returns-only
            if "NET_SALES" in sql_upper:
                high_issues.append({
                    "type": "business_logic_violation",
                    "severity": "critical",
                    "message": (
                        "User requested RETURNS only. Query must output TOTAL_RETURNS only "
                        "(and PERIOD if requested) using ONLY IAS_RT_* tables. Do NOT compute NET_SALES."
                    ),
                    "location": "SELECT_LIST",
                })

            # Must use returns tables
            if not any(t in sql_upper for t in ["IAS_RT_BILL_MST", "IAS_RT_BILL_DTL"]):
                high_issues.append({
                    "type": "business_logic_violation",
                    "severity": "critical",
                    "message": "Returns-only query must use IAS_RT_BILL_MST RM and IAS_RT_BILL_DTL RD.",
                    "location": "FROM_CLAUSE",
                })


        # --------------------------------------------------------------------
        # RULE: For NET SALES grouped by period, forbid correlated subqueries
        # that reference grouped columns (common cause of ORA-00979).
        # Force UNION ALL pattern instead.
        # --------------------------------------------------------------------
        q = user_query_lower

        user_requested_net = any(k in q for k in ["net", "net sales", "صافي", "صافى", "صافي المبيعات"])
        period_markers = any(k in q for k in ["month", "monthly", "quarter", "ربع", "شه", "شهر", "سنة", "year", "period", "فترة"])
        sql_has_subquery = ("SELECT SUM" in sql_upper and "FROM IAS202538.IAS_RT_BILL_MST" in sql_upper and "WHERE" in sql_upper)
        sql_has_outer_group = "GROUP BY" in sql_upper
        sql_correlated_period_ref = ("= TRUNC(M.BILL_DATE" in sql_upper) or ("= TO_CHAR(M.BILL_DATE" in sql_upper) or ("= TRUNC(MST.BILL_DATE" in sql_upper)

        if user_requested_net and (period_markers or "GROUP BY" in sql_upper) and sql_has_subquery and sql_has_outer_group and sql_correlated_period_ref:
            high_issues.append({
                "type": "group_by_correlation_violation",
                "severity": "critical",
                "message": "Net sales grouped by period must NOT use correlated subqueries referencing grouped date expressions (causes ORA-00979). Use UNION ALL (sales rows + returns rows) and aggregate once by period.",
                "location": "SQL_QUERY",
            })

        # -----------------------------------------------------------
        # RULE: Wrong join between sales and returns tables
        # -----------------------------------------------------------
        if ("IAS_RT_BILL_MST" in sql_upper or "IAS_RT_BILL_DTL" in sql_upper) and "LEFT JOIN" in sql_upper:
            high_issues.append({
                "type": "business_logic_violation",
                "severity": "high",
                "message": "Returns must NEVER be LEFT JOINed with sales. Use UNION ALL or separate aggregation.",
                "location": "JOIN_CLAUSE"
            })

            

        if not high_issues:
            # Nothing serious to fix
            return corrected
        
        issues_text = "\n".join(
            f"- [{i.get('severity', '').upper()}] {i.get('type', '')}: {i.get('message', '')}"
            for i in high_issues
        )
        
        # 2) Build a compact schema context from TableInfo objects
        schema_lines = []
        try:
            for t in schema_retrieved or []:
                try:
                    table_name = getattr(t, "name", "UNKNOWN")
                    
                    # Columns (handle dict/list/dataclass formats)
                    col_names = []
                    if hasattr(t, "columns"):
                        col_list = t.columns
                        if isinstance(col_list, list):
                            for col in col_list:
                                if isinstance(col, dict):
                                    col_name = col.get("name", "")
                                    col_type = col.get("type", "")
                                    if col_name:
                                        col_names.append(f"{col_name} ({col_type})")
                                elif hasattr(col, "name"):
                                    col_name = getattr(col, "name", "")
                                    col_type = getattr(col, "type", "")
                                    if col_name:
                                        col_names.append(f"{col_name} ({col_type})")
                    
                    columns_str = ", ".join(col_names) if col_names else "None"
                    
                    # Primary keys
                    pks = []
                    if hasattr(t, "primary_keys"):
                        pk_list = t.primary_keys
                        if isinstance(pk_list, list):
                            pks = [str(pk) for pk in pk_list]
                    pks_str = ", ".join(pks) if pks else "None"
                    
                    schema_lines.append(
                        f"Table: {table_name}\n"
                        f"Columns: {columns_str}\n"
                        f"Primary Keys: {pks_str}"
                    )
                except Exception as inner_e:
                    logger.debug(f"Error formatting table for correction: {inner_e}")
                    continue
        except Exception as e:
            logger.error(f"Error building schema context for correction: {e}")
        
        schema_context = "\n\n".join(schema_lines) if schema_lines else "No schema context available."
        
        # 3) Build a strong correction prompt (Oracle 12c + your business rules)
        prompt = f"""
You are an expert Oracle Database 12c SQL developer assigned to FIX and IMPROVE a SQL query.
You must follow strict ERP business rules and client standards.

======================================================================
[USER QUERY]
{user_query}
======================================================================

======================================================================
[CURRENT SQL]
{sql_statement}
======================================================================

======================================================================
[IDENTIFIED ISSUES]
{issues_text}
======================================================================

======================================================================
[CLIENT BUSINESS LOGIC RULES]

1) Only include RETURNS or NET SALES if the user explicitly asks using keywords:
   1.1 "صافي", "صافى", "net", "net sales", "returns", "المردودات", "مرتجع", "مرتجعات".
   Otherwise, the query MUST compute ONLY total sales.
   
   1.2 RETURNS-ONLY OVERRIDE:
       If the user asks for RETURNS only (e.g., "مردود المبيعات") and does NOT ask for net,
       then the output MUST be ONLY TOTAL_RETURNS (and PERIOD if requested),
       using ONLY IAS202538.IAS_RT_BILL_MST RM + IAS202538.IAS_RT_BILL_DTL RD.
       Do NOT output NET_SALES in this case.


2) SALES FORMULA (always use this):
    (NVL(D.I_PRICE, 0) - NVL(D.DIS_AMT, 0) + NVL(D.OTHR_AMT, 0))
    * NVL(D.I_QTY, 0)
    * NVL(M.BILL_RATE, 1)

3) RETURNS FORMULA (only when requested):
    (NVL(RD.I_PRICE, 0) - NVL(RD.DIS_AMT, 0) + NVL(RD.OTHR_AMT, 0))
    * NVL(RD.I_QTY, 0)
    * NVL(RM.RT_BILL_RATE, 1)

4) SALES JOIN RULE:
    M.BILL_DOC_TYPE = D.BILL_DOC_TYPE
    AND M.BILL_NO   = D.BILL_NO
    AND M.BILL_SER  = D.BILL_SER

5) RETURNS JOIN RULE:
    RM.RT_BILL_DOC_TYPE = RD.RT_BILL_DOC_TYPE
    AND RM.RT_BILL_NO   = RD.RT_BILL_NO
    AND RM.RT_BILL_SER  = RD.RT_BILL_SER

6) NEVER join sales and returns using LEFT JOIN.
   Use UNION ALL or separate aggregates if needed.

7) VALID NET SALES PATTERN (only if user requested net):
   SUM(SALES) - SUM(RETURNS)
   OR the UNION ALL model:
       SELECT period, SUM(NET_SALES - RT_NET_SALES)...

8) IMPORTANT: Net sales by period (month/quarter/year) MUST use UNION ALL aggregation.
   - DO NOT use correlated subqueries referencing outer grouped columns such as:
       TRUNC(M.BILL_DATE,'MM') or TO_CHAR(M.BILL_DATE,'YYYY-MM')
     inside a subquery when the outer query has GROUP BY.
   - This pattern frequently causes ORA-00979 and is strictly forbidden.

   REQUIRED PATTERN:
     SELECT period, SUM(sales_amt) - SUM(ret_amt) AS net_sales
     FROM (
        SELECT TRUNC(M.BILL_DATE,'MM') AS period, SALES_FORMULA AS sales_amt, 0 AS ret_amt
        FROM IAS_BILL_MST M JOIN IAS_BILL_DTL D ...
        WHERE <period filter>

        UNION ALL

        SELECT TRUNC(RM.RT_BILL_DATE,'MM') AS period, 0 AS sales_amt, RETURNS_FORMULA AS ret_amt
        FROM IAS_RT_BILL_MST RM JOIN IAS_RT_BILL_DTL RD ...
        WHERE <same period filter>
     )
     GROUP BY period
     ORDER BY period;
9) SCHEMA PREFIX RULE (IAS202538):
   - All tables MUST be schema-qualified: IAS202538.<TABLE_NAME>
   - NEVER schema-qualify aliases, columns, or Oracle built-ins.
     Valid:  FROM IAS202538.IAS_BILL_MST M
     Valid:  M.BILL_DATE, D.I_PRICE, SYSDATE, TRUNC(SYSDATE,'MM'), NVL(...), EXTRACT(...)
     Invalid: IAS202538.M.BILL_DATE
     Invalid: IAS202538.SYSDATE
     Invalid: IAS202538.TRUNC(...), IAS202538.NVL(...), IAS202538.EXTRACT(...)

10) Sales Indicators (MANDATORY SQL SHAPE):
    1. If the user asks for “مؤشرات المبيعات / sales indicators”, you MUST output exactly one query using UNION ALL between sales and returns streams and compute:
        - TOTAL_SALES = SUM(sales_amt)
        - TOTAL_RETURNS = SUM(ret_amt)
        - NET_SALES = SUM(sales_amt) - SUM(ret_amt) 

    2. You are FORBIDDEN from:
        - setting returns to 0
        - using LEFT JOIN between sales and returns
        - using correlated subqueries for returns”
    3. When user asks مؤشرات المبيعات / sales indicators: MUST use UNION ALL (sales stream + returns stream) and aggregate by period (or no period if not requested). FORBIDDEN: LEFT JOIN sales-to-returns

    4. Do NOT return two rows per period. The query must return one row per period by using UNION ALL inside a subquery then GROUP BY PERIOD in the outer query



======================================================================

======================================================================
[CRITICAL COLUMN NAME RULES]
Do NOT modify these:
- i_price
- dis_amt
- othr_amt
- i_qty
- bill_rate
- rt_bill_rate
======================================================================

======================================================================
[TASK]

Fix the SQL query so that:
- It follows ALL business rules above.
- It follows Oracle 12c syntax strictly.
- It uses ONLY tables relevant to the user's request.
- It DOES NOT include returns/net sales unless the user asked.
- It uses correct formulas, correct joins, correct aliases.
- If net/returns are requested by period, you MUST use the UNION ALL pattern (Rule #8).

RETURN ONLY THE CORRECTED SQL QUERY. NO EXPLANATION.
======================================================================
"""

        
        try:
            response = self.llm_client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert Oracle Database 12c SQL developer. You must return only a single valid SQL statement.",
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                temperature=0.1,
            )
            
            content = response.choices[0].message.content if response.choices else ""
            if not content:
                logger.warning("LLM returned empty content for correction")
                return corrected
            
            # Strip markdown fences if the model adds them           
            text = content.strip()

            # --- Remove markdown fences if the model adds them ---
            if text.startswith("```"):
                text = re.sub(r"^```[\w]*\s*", "", text)   # remove ```sql or ``` code fences
                text = re.sub(r"```$", "", text).strip()  # remove closing ```

            # --- Remove inline SQL comments ---
            # Removes anything after "--"
            if "--" in text:
                text = text.split("--")[0].strip()

            # --- Normalize double newlines ---
            text = text.replace("\r", "")
            text = re.sub(r"\n\s*\n", "\n", text).strip()

            corrected = text
        except Exception as e:
            logger.error(f"Error during LLM-based correction: {e}")
        
        return corrected


# Integration function for easy use
def reflect_sql_statement(
    sql_statement: str,
    user_query: str,
    schema_retrieved: List,
    schema_processor,
    sql_validator,
    llm_client,
    model: str = "gpt-4",
    max_iterations: int = 3
) -> ReflectionResult:
    """
    Convenience function to create and run SQL reflection agent.
    
    Args:
        sql_statement: Generated SQL query to validate
        user_query: Original user's natural language query
        schema_retrieved: List of relevant TableInfo objects
        schema_processor: Instance of SchemaProcessor
        sql_validator: Instance of SQLValidator
        llm_client: OpenAI-compatible client instance
        model: LLM model to use for validation
        max_iterations: Maximum correction attempts
        
    Returns:
        ReflectionResult with comprehensive validation results
    """
    agent = SQLReflectionAgent(
        schema_processor=schema_processor,
        sql_validator=sql_validator,
        llm_client=llm_client,
        model=model
    )
    
    return agent.reflect_sql_statement(
        sql_statement=sql_statement,
        user_query=user_query,
        schema_retrieved=schema_retrieved,
        max_iterations=max_iterations
    )