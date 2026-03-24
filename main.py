from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Dict, Optional
import os
from datetime import datetime
import uuid
import logging
from dotenv import load_dotenv
import re
# Import your text-to-SQL system
from text_to_sql_system import TextToSQLGenerator
from text_to_sql_system import SchemaProcessor
from motor.motor_asyncio import AsyncIOMotorClient
from reflect_sql_agent import reflect_sql_statement
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
# Load environment variables

# Add this near your app initialization
MONGODB_URI = "mongodb+srv://mohammadabdallahdeveloper_db_user:RpBZPYCUeNURFkGq@cluster0.rgvkyz1.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
mongo_client = AsyncIOMotorClient(MONGODB_URI)
db = mongo_client["query_logs"]  # database name
logs_collection = db["logs"]  # collection name

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Text-to-SQL API",
    description="Bilingual Text-to-SQL conversion system for ERP database",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Pydantic models
class QueryRequest(BaseModel):
    query: str
    language: str = "en"  # 'en' or 'ar'
    conversation_id: Optional[str] = None

class ValidationMetadata(BaseModel):
    is_valid: bool
    confidence_score: float
    was_corrected: bool
    issues_count: int
    critical_issues: int
    high_issues: int

class QueryResponse(BaseModel):
    sql_query: str
    tables_used: List[str]
    explanation: Optional[str] = None
    conversation_id: str
    language: str
    timestamp: datetime
    # New fields
    user_query: str
    original_sql: str
    model: str
    validation: ValidationMetadata

class ExplainRequest(BaseModel):
    sql_query: str
    language: str = "en"
    conversation_id: Optional[str] = None

class ConversationHistoryResponse(BaseModel):
    conversation_id: str
    messages: List[Dict]
    total_messages: int

class ErrorResponse(BaseModel):
    error: str
    details: Optional[str] = None
    timestamp: datetime

# Global variables
text_to_sql_generator = None

# Language translations
TRANSLATIONS = {
    "en": {
        "welcome": "Welcome to Text-to-SQL Converter",
        "query_placeholder": "Enter your database query in natural language...",
        "generate": "Generate SQL",
        "explain": "Explain Query",
        "clear": "Clear History",
        "history": "History",
        "sql_result": "Generated SQL Query",
        "explanation": "Explanation",
        "tables_used": "Tables Used",
        "error": "Error",
        "processing": "Processing...",
        "no_history": "No conversation history available",
        "language": "Language",
        "english": "English",
        "arabic": "Arabic"
    },
    "ar": {
        "welcome": "مرحباً بكم في محول النص إلى SQL",
        "query_placeholder": "أدخل استعلام قاعدة البيانات باللغة الطبيعية...",
        "generate": "إنشاء SQL",
        "explain": "شرح الاستعلام",
        "clear": "مسح السجل",
        "history": "السجل",
        "sql_result": "استعلام SQL المُولد",
        "explanation": "الشرح",
        "tables_used": "الجداول المستخدمة",
        "error": "خطأ",
        "processing": "جاري المعالجة...",
        "no_history": "لا يوجد سجل محادثة متاح",
        "language": "اللغة",
        "english": "الإنجليزية",
        "arabic": "العربية"
    }
}

@app.on_event("startup")
async def startup_event():
    """Initialize the text-to-SQL generator on startup"""
    global text_to_sql_generator
    
    api_key = os.getenv('OPENAI_API_KEY', 'not-needed')

    text_to_sql_generator = TextToSQLGenerator(
    api_key,
    model="Qwen/Qwen3-Coder-30B-A3B-Instruct"
)
    
    try:
        text_to_sql_generator = TextToSQLGenerator(api_key, model="Qwen/Qwen3-Coder-30B-A3B-Instruct")
        
        # Try to load enhanced schema if available
        if os.path.exists('erp_schema.json'):
            enhanced_processor = SchemaProcessor()
            text_to_sql_generator.schema_processor = enhanced_processor
            logger.info("Loaded enhanced schema processor")
        
        logger.info("Text-to-SQL generator initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize text-to-SQL generator: {e}")
        raise RuntimeError(f"Initialization failed: {e}")

def detect_language(text: str) -> str:
    """Simple regex-based language detection"""
    # Arabic pattern - checks for Arabic Unicode range
    arabic_pattern = r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]'
    
    if re.search(arabic_pattern, text):
        return "ar"
    else:
        return "en"  # Default to English
    
async def translate_to_english(text: str) -> str:
    """Translate Arabic text to English using the text-to-SQL generator"""
    if not text_to_sql_generator:
        raise RuntimeError("Text-to-SQL generator not initialized")
    
    translation_prompt = f"Translate the following Arabic text to English:\n\n{text}"
    response = await text_to_sql_generator.generate(translation_prompt)
    return response.strip()
    
@app.get("/", response_class=FileResponse)
async def read_root():
    """Serve the main HTML page"""
    return FileResponse('static/index.html')

@app.get("/translations/{language}")
async def get_translations(language: str):
    """Get translations for the specified language"""
    if language not in TRANSLATIONS:
        raise HTTPException(status_code=400, detail="Unsupported language")
    
    return TRANSLATIONS[language]

@app.post("/api/query", response_model=QueryResponse)
async def generate_sql_query(request: QueryRequest):
    """Generate SQL query from natural language"""
    try:
        if not text_to_sql_generator:
            raise HTTPException(status_code=500, detail="Text-to-SQL generator not initialized")
        
        # Generate conversation ID if not provided
        conversation_id = request.conversation_id or str(uuid.uuid4())
        print("conversation id", conversation_id)
        # Enhance query with language context
        enhanced_query = request.query
        
        lang = detect_language(request.query)
        if lang == "ar":
            # Add context for Arabic queries
            print("translatiinnnnnngggg")
            enhanced_query = await translate_to_english(request.query)
            
        
        print("enhanced query", enhanced_query)
        
        # Generate SQL
        sql_query, tables_used, schema_context = text_to_sql_generator.generate_sql(
            enhanced_query, 
            conversation_id=conversation_id
        )
        print(
            'sql query', sql_query
        )
        reflection_result = reflect_sql_statement(
            sql_statement=sql_query,
            user_query=enhanced_query,
            schema_retrieved=tables_used,
            schema_processor=text_to_sql_generator.schema_processor,
            sql_validator=None,
            llm_client=text_to_sql_generator.client,
            model=text_to_sql_generator.model,
            max_iterations=3
        )
        
        final_sql = sql_query
        if not reflection_result.is_valid and reflection_result.corrected_sql:
            if reflection_result.confidence_score >= 50:
                final_sql = reflection_result.corrected_sql
                if 'OTH_AMT' in final_sql:
                    final_sql = final_sql.replace('OTH_AMT', 'OTHR_AMT')
        validation_metadata = {
            "is_valid": reflection_result.is_valid,
            "confidence_score": reflection_result.confidence_score,
            "was_corrected": sql_query != final_sql
        }
                # Log to MongoDB
        await logs_collection.insert_one({
            "user_query": request.query,
            "sql_query": final_sql,  # Log the final SQL (possibly corrected)
            "original_sql": sql_query,  # Keep track of original
            "model": "local",
            "conversation_id": conversation_id,
            "language": request.language,
            "tables_used": tables_used,
            "timestamp": datetime.now(),
            # Add validation metadata
            "validation": {
                "is_valid": reflection_result.is_valid,
                "confidence_score": reflection_result.confidence_score,
                "was_corrected": sql_query != final_sql,
                "issues_count": len(reflection_result.issues),
                "critical_issues": len([i for i in reflection_result.issues if i['severity'] == 'critical']),
                "high_issues": len([i for i in reflection_result.issues if i['severity'] == 'high']),
            }
        })
        return QueryResponse(
            sql_query=final_sql,
            tables_used=tables_used,
            conversation_id=conversation_id,
            language=request.language,
            timestamp=datetime.now(),
            user_query=request.query,
            original_sql=sql_query,
            model="local",
            validation=ValidationMetadata(
                is_valid=reflection_result.is_valid,
                confidence_score=reflection_result.confidence_score,
                was_corrected=sql_query != final_sql,
                issues_count=len(reflection_result.issues),
                critical_issues=len([i for i in reflection_result.issues if i['severity'] == 'critical']),
                high_issues=len([i for i in reflection_result.issues if i['severity'] == 'high'])
            )
        )
    except Exception as e:
        logger.error(f"Error generating SQL query: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/explain")
async def explain_sql_query(request: ExplainRequest):
    """Explain what a SQL query does"""
    try:
        if not text_to_sql_generator:
            raise HTTPException(status_code=500, detail="Text-to-SQL generator not initialized")
        
        conversation_id = request.conversation_id or str(uuid.uuid4())
        
        # Generate explanation with language context
        if request.language == "ar":
            explanation_prompt = f"Explain this SQL query in Arabic: {request.sql_query}"
        else:
            explanation_prompt = request.sql_query
        
        explanation = text_to_sql_generator.explain_query(
            explanation_prompt,
            conversation_id=conversation_id
        )
        
        return {
            "explanation": explanation,
            "conversation_id": conversation_id,
            "language": request.language,
            "timestamp": datetime.now()
        }
        
    except Exception as e:
        logger.error(f"Error explaining SQL query: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/conversation/{conversation_id}/history", response_model=ConversationHistoryResponse)
async def get_conversation_history(conversation_id: str):
    """Get conversation history for a specific conversation"""
    try:
        if not text_to_sql_generator:
            raise HTTPException(status_code=500, detail="Text-to-SQL generator not initialized")
        
        history = text_to_sql_generator.get_conversation_history(conversation_id)
        
        return ConversationHistoryResponse(
            conversation_id=conversation_id,
            messages=history,
            total_messages=len(history)
        )
        
    except Exception as e:
        logger.error(f"Error getting conversation history: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/conversation/{conversation_id}")
async def clear_conversation(conversation_id: str):
    """Clear conversation history"""
    try:
        if not text_to_sql_generator:
            raise HTTPException(status_code=500, detail="Text-to-SQL generator not initialized")
        
        text_to_sql_generator.clear_conversation(conversation_id)
        
        return {
            "message": "Conversation history cleared",
            "conversation_id": conversation_id,
            "timestamp": datetime.now()
        }
        
    except Exception as e:
        logger.error(f"Error clearing conversation: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(),
        "generator_status": "initialized" if text_to_sql_generator else "not_initialized"
    }

@app.get("/api/schema/summary")
async def get_schema_summary():
    """Get schema summary information"""
    try:
        if not text_to_sql_generator or not text_to_sql_generator.schema_processor:
            raise HTTPException(status_code=500, detail="Schema processor not available")
        
        tables = text_to_sql_generator.schema_processor.tables
        
        # Create summary
        domains = {}
        for table in tables.values():
            domain = table.domain
            domains[domain] = domains.get(domain, 0) + 1
        
        return {
            "total_tables": len(tables),
            "tables_by_domain": domains,
            "table_names": list(tables.keys()),
            "timestamp": datetime.now()
        }
        
    except Exception as e:
        logger.error(f"Error getting schema summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Error handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return ErrorResponse(
        error=exc.detail,
        timestamp=datetime.now()
    )

@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {exc}")
    return ErrorResponse(
        error="Internal server error",
        details=str(exc),
        timestamp=datetime.now()
    )

if __name__ == "__main__":
    import uvicorn
    
    # Create static directory if it doesn't exist
    os.makedirs("static", exist_ok=True)
    
    # Run the application
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )