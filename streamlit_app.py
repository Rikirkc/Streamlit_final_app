import streamlit as st
from dotenv import load_dotenv
import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_community.document_loaders import PyPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.vectorstores import Chroma
from langchain.retrievers import ContextualCompressionRetriever
from langchain.retrievers.document_compressors.flashrank_rerank import FlashrankRerank
from langchain.chains.retrieval_qa.base import RetrievalQA
from flashrank import Ranker

__import__('pysqlite3')
import sys
sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')

load_dotenv()

GEMINI_API_KEY = st.secrets['GEMINI_API_KEY']

# Function to load and convert a document
def load_and_convert_document(document_path: str, **kwargs):
    if kwargs.get("parse_function"):
        documents = PyPDFLoader(document_path).load()
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=kwargs.get("chunk_size", 2048),
            chunk_overlap=kwargs.get("chunk_size", 2048) // 2,
        )
        splitted_documents = text_splitter.split_documents(documents)
        for index, text in enumerate(splitted_documents):
            text.metadata["id"] = index

        doc_name = kwargs.get('doc_name', 'Safeword')
        embedding = GoogleGenerativeAIEmbeddings(model=kwargs.get("embedding_model", "models/text-embedding-004"), google_api_key=GEMINI_API_KEY)
        persist_dir = f"./DB/{str(doc_name).split('.')[0]}_db"
        return Chroma.from_documents(
            splitted_documents, embedding, persist_directory=persist_dir
        ).as_retriever(search_kwargs={"k": kwargs.get("retriever_top_documents", 5)})
    return None


# Function to perform RAG with Flash Reranking
def perform_rag_with_flash_reranking(user_question, **kwargs):
    if kwargs.get("parse_function"):
        try:
            llm_model_name = "gemini-1.5-flash"
            llm_kwargs = {
                "temperature": 0,
                "max_tokens": kwargs.get("maximum_output_tokens", 2048),
                "top_p": 1,
            }
            flash_rank_top_n = 4

            llm_model = ChatGoogleGenerativeAI(
                model=llm_model_name,
                temperature=llm_kwargs["temperature"],
                max_tokens=llm_kwargs["max_tokens"],
                top_p=llm_kwargs["top_p"],
                api_key=GEMINI_API_KEY
            )

            model_name = "ms-marco-MultiBERT-L-12"
            flashrank_client = Ranker(model_name=model_name)

            compressor_model = FlashrankRerank(client=flashrank_client, top_n=flash_rank_top_n)
            compression_retriever = ContextualCompressionRetriever(
                base_compressor=compressor_model,
                base_retriever=kwargs.get("retriever"),
            )

            chain = RetrievalQA.from_chain_type(
                llm=llm_model,
                chain_type="stuff",
                retriever=compression_retriever,
            )

            answer = chain.invoke(user_question)
            return answer["result"]
        except Exception as exp:
            st.error(f"Exception occurred: {exp}")
            return None


def load_vector_db_from_cache(persist_dir):
    return Chroma(persist_directory=persist_dir, embedding_function=GoogleGenerativeAIEmbeddings(model="models/text-embedding-004", google_api_key=
                                                                                                 GEMINI_API_KEY))


# Streamlit UI
st.title("Enhanced RAG System with Google Gemini")

base_persist_dir = './DB'
if not os.path.exists(base_persist_dir):
    os.makedirs(base_persist_dir)

persist_dirs = [os.path.join(base_persist_dir, d) for d in os.listdir(base_persist_dir)]
persist_dirs_final = [items.split("/")[-1] for items in persist_dirs]

st.sidebar.header("Dataset Selection")
selected_dir = st.sidebar.selectbox("Select a dataset to query:", ["None"] + persist_dirs_final)

retriever = None
if selected_dir != "None":
    retriever = load_vector_db_from_cache(persist_dir=os.path.join(base_persist_dir, selected_dir)).as_retriever(search_kwargs={'k': 5})
    st.sidebar.success(f'Dataset selected: {selected_dir}')

uploaded_file = st.file_uploader("Upload a PDF document", type=["pdf"])
if uploaded_file:
    st.info(f'Processing uploaded document: {uploaded_file.name}')
    with open("uploaded_document.pdf", "wb") as f:
        f.write(uploaded_file.read())

    document_path = os.path.abspath("uploaded_document.pdf")
    retriever = load_and_convert_document(
        document_path=document_path,
        parse_function=True,
        embedding_model="models/text-embedding-004",
        retriever_top_documents=5,
        chunk_size=2048,
        doc_name=uploaded_file.name,
    )
    if retriever:
        st.success(f"Document '{uploaded_file.name}' has been processed and embedded in the vector database.")
        st.sidebar.success(f"New dataset added: {uploaded_file.name}")
    else:
        st.error("Failed to process the uploaded document.")

# Question Answering Section
if retriever:
    st.header("Ask Questions about the Selected Document")
    question = st.text_input("Enter your question:")

    if st.button("Get Answer") and question.strip():
        with st.spinner("Retrieving answer..."):
            new_answer = perform_rag_with_flash_reranking(
                question,
                retriever=retriever,
                parse_function=True,
                verbose=-1,
                maximum_output_tokens=2048,
            )
        if new_answer:
            st.success("Answer retrieved!")
            st.write(f"**Answer:** {new_answer}")
        else:
            st.error("Failed to retrieve an answer.")

# Clear Session
if st.sidebar.button("Clear Session"):
    if os.path.exists("uploaded_document.pdf"):
        os.remove("uploaded_document.pdf")
    st.sidebar.success("Session cleared! Reload the app to start over.")
