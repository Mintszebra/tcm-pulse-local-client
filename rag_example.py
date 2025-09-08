import os
import sys
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from llama_index.core import (
    VectorStoreIndex,
    SimpleDirectoryReader,
    Settings,
    StorageContext,
    load_index_from_storage,
)

from llama_index.core.node_parser import SentenceSplitter
from llama_index.llms.openai import OpenAI
from llama_index.embeddings.openai import OpenAIEmbedding

load_dotenv()

class RAGApplication:
    def __init__(
        self,
        data_dir: str = "data",
        persist_dir: str = "./storage",
        chunk_size: int = 512,
        chunk_overlap: int = 50,
    ):
        self.data_dir = Path(data_dir)
        self.persist_dir = Path(persist_dir)
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.index: Optional[VectorStoreIndex] = None
        
        self._setup_llm()
        
    def _setup_llm(self):
        api_key = os.getenv("OPENAI_API_KEY")     #這邊讀取環境變數，建立一個檔案叫做.env
        if not api_key:
            raise ValueError(
                "OPENAI_API_KEY not found. Please set it in your .env file"
            )
        
        Settings.llm = OpenAI(
            model="o3",
            temperature=0.1,
            api_key=api_key,
        )
        Settings.embed_model = OpenAIEmbedding(
            model="text-embedding-ada-002",
            api_key=api_key,
        )
        
        Settings.chunk_size = self.chunk_size
        Settings.chunk_overlap = self.chunk_overlap
        
    def load_documents(self) -> List:
        if not self.data_dir.exists():
            raise FileNotFoundError(f"Data directory {self.data_dir} not found")
        
        print(f"Loading documents from {self.data_dir}...")
        documents = SimpleDirectoryReader(
            input_dir=str(self.data_dir),
            recursive=True,
            filename_as_id=True,
        ).load_data()
        
        print(f"Loaded {len(documents)} documents")
        return documents
    
    def build_index(self, force_rebuild: bool = False):
        if not force_rebuild and self.persist_dir.exists():
            print(f"Loading existing index from {self.persist_dir}...")
            storage_context = StorageContext.from_defaults(
                persist_dir=str(self.persist_dir)
            )
            self.index = load_index_from_storage(storage_context)
            print("Index loaded successfully!")
        else:
            documents = self.load_documents()
            
            if not documents:
                print("No documents found in the data directory.")
                print("Please add some text or PDF files to the 'data' directory.")
                return
            
            print("Building new index...")
            parser = SentenceSplitter(
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
            )
            
            nodes = parser.get_nodes_from_documents(documents)
            print(f"Created {len(nodes)} nodes from documents")
            
            self.index = VectorStoreIndex(nodes)
            
            print(f"Persisting index to {self.persist_dir}...")
            self.index.storage_context.persist(persist_dir=str(self.persist_dir))
            print("Index built and persisted successfully!")
    
    def query(self, query_text: str, similarity_top_k: int = 3) -> str:
        if self.index is None:
            raise ValueError("Index not built. Please run build_index() first.")
        
        query_engine = self.index.as_query_engine(
            similarity_top_k=similarity_top_k,
            response_mode="tree_summarize",
        )
        
        response = query_engine.query(query_text)
        return str(response)
    
    def chat(self):
        if self.index is None:
            print("No index found. Building index first...")
            self.build_index()
            
            if self.index is None:
                print("Failed to build index. Exiting.")
                return
        
        chat_engine = self.index.as_chat_engine(
            chat_mode="condense_question",
            verbose=False,
        )
        
        print("\nRAG Chat Interface")
        print("=" * 50)
        print("Type 'quit' or 'exit' to end the conversation")
        print("Type 'clear' to clear the conversation history")
        print("=" * 50)
        
        while True:
            try:
                user_input = input("\nYou: ").strip()
                
                if user_input.lower() in ["quit", "exit"]:
                    print("Goodbye!")
                    break
                
                if user_input.lower() == "clear":
                    chat_engine.reset()
                    print("Conversation history cleared.")
                    continue
                
                if not user_input:
                    continue
                
                print("\nAssistant: ", end="", flush=True)
                response = chat_engine.chat(user_input)
                print(response)
                
            except KeyboardInterrupt:
                print("\nGoodbye!")
                break
            except Exception as e:
                print(f"\nError: {e}")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="RAG Application using LlamaIndex"
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data",
        help="Directory containing documents to index",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Force rebuild the index even if it exists",
    )
    parser.add_argument(
        "--query",
        type=str,
        help="Single query to run (non-interactive mode)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=512,
        help="Chunk size for text splitting",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=50,
        help="Chunk overlap for text splitting",
    )
    
    args = parser.parse_args()
    
    try:
        rag_app = RAGApplication(
            data_dir=args.data_dir,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
        )
        
        if args.query:
            rag_app.build_index(force_rebuild=args.rebuild)
            if rag_app.index:
                print(f"\nQuery: {args.query}")
                print("-" * 50)
                response = rag_app.query(args.query)
                print(f"Response: {response}")
        else:
            rag_app.build_index(force_rebuild=args.rebuild)
            rag_app.chat()
            
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()