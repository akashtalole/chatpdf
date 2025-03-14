from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import *
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
import os
import logging
from azure.search.documents.models import QueryType
from Utilities.embeddings import generateEmbeddings
from azure.search.documents.indexes.models import (  
    SearchIndex,  
    SearchField,  
    SearchFieldDataType,  
    SimpleField,  
    SearchableField,  
    SearchIndex,  
    SemanticConfiguration,  
    SemanticField,  
    SearchField,  
    SemanticPrioritizedFields,
    VectorSearch,  
    HnswAlgorithmConfiguration,  
)
from Utilities.envVars import *
from tenacity import retry, wait_random_exponential, stop_after_attempt  
import openai
from openai import OpenAI, AzureOpenAI
from azure.search.documents.models import VectorizedQuery
from azure.identity import ClientSecretCredential, AzureAuthorityHosts

def deleteSearchIndex(indexName):
    authority = AzureAuthorityHosts.AZURE_PUBLIC_CLOUD
    credentials = ClientSecretCredential(TenantId, ClientId, ClientSecret, authority=authority)
    indexClient = SearchIndexClient(endpoint=f"https://{SearchService}.search.windows.net/",
            credential=credentials)
    if indexName in indexClient.list_index_names():
        logging.info(f"Deleting {indexName} search index")
        indexClient.delete_index(indexName)
    else:
        logging.info(f"Search index {indexName} does not exist")
        
def createSearchIndex(indexType, indexName):
    authority = AzureAuthorityHosts.AZURE_PUBLIC_CLOUD
    credentials = ClientSecretCredential(TenantId, ClientId, ClientSecret, authority=authority)
    indexClient = SearchIndexClient(endpoint=f"https://{SearchService}.search.windows.net/",
            credential=credentials)
    if indexName not in indexClient.list_index_names():
        if indexType == "cogsearchvs":
            index = SearchIndex(
                name=indexName,
                fields=[
                            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
                            SearchableField(name="content", type=SearchFieldDataType.String,
                                            searchable=True, retrievable=True, analyzer_name="en.microsoft"),
                            SearchField(name="content_vector", type=SearchFieldDataType.Collection(SearchFieldDataType.Single), 
                                vector_search_dimensions=1536, vector_search_profile_name="myHnswProfile"),  
                            SearchableField(name="metadata", type=SearchFieldDataType.String, searchable=True, retrievable=True),
                ],
                vector_search = VectorSearch(
                    algorithms=[
                        HnswAlgorithmConfiguration(
                            name="default",
                            parameters=HnswParameters(  
                                m=4,  
                                ef_construction=400,  
                                ef_search=500,  
                                metric=VectorSearchAlgorithmMetric.COSINE,  
                            ),
                        ),
                        ExhaustiveKnnAlgorithmConfiguration(
                            name="default_exhaustive_knn",
                            kind=VectorSearchAlgorithmKind.EXHAUSTIVE_KNN,
                            parameters=ExhaustiveKnnParameters(
                                metric=VectorSearchAlgorithmMetric.COSINE
                            ),
                        ),
                    ],  
                    profiles=[  
                        VectorSearchProfile(  
                            name="myHnswProfile",  
                            algorithm_configuration_name="default",  
                        ),
                    ],
                ),
                semantic_search = SemanticSearch(configurations=[SemanticConfiguration(
                    name="mySemanticConfig",
                    prioritized_fields=SemanticPrioritizedFields(
                        content_fields=[SemanticField(field_name="content")]
                    )
                )])
            )
        elif indexType == "cogsearch":
            index = SearchIndex(
                name=indexName,
                fields=[
                            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
                            SearchableField(name="content", type=SearchFieldDataType.String,
                                            searchable=True, retrievable=True, analyzer_name="en.microsoft"),
                            SimpleField(name="sourcefile", type="Edm.String", filterable=True, facetable=True),
                ],
                semantic_search = SemanticSearch(configurations=[SemanticConfiguration(
                    name="mySemanticConfig",
                    prioritized_fields=SemanticPrioritizedFields(
                        title_field=SemanticField(field_name="content"),
                        keywords_fields=[SemanticField(field_name="sourcefile")],
                        content_fields=[SemanticField(field_name="content")]
                    )
                )])
            )

        try:
            print(f"Creating {indexName} search index")
            indexClient.create_index(index)
        except Exception as e:
            print(e)
    else:
        logging.info(f"Search index {indexName} already exists")

def createSections(indexType, embeddingModelType, fileName, docs):
    counter = 1
    if indexType == "cogsearchvs":
        for i in docs:
            yield {
                "id": f"{fileName}-{counter}".replace(".", "_").replace(" ", "_").replace(":", "_").replace("/", "_").replace(",", "_").replace("&", "_"),
                "content": i.page_content,
                "contentVector": generateEmbeddings(embeddingModelType, i.page_content),
                "sourcefile": os.path.basename(fileName)
            }
            counter += 1
    elif indexType == "cogsearch":
        for i in docs:
            yield {
                "id": f"{fileName}-{counter}".replace(".", "_").replace(" ", "_").replace(":", "_").replace("/", "_").replace(",", "_").replace("&", "_"),
                "content": i.page_content,
                "sourcefile": os.path.basename(fileName)
            }
            counter += 1

def indexSections(indexType, embeddingModelType, fileName, indexName, docs):

    logging.info("Total docs: " + str(len(docs)))
    sections = createSections(indexType, embeddingModelType, fileName, docs)
    logging.info(f"Indexing sections from '{fileName}' into search index '{indexName}'")
    authority = AzureAuthorityHosts.AZURE_PUBLIC_CLOUD
    credentials = ClientSecretCredential(TenantId, ClientId, ClientSecret, authority=authority)
    searchClient = SearchClient(endpoint=f"https://{SearchService}.search.windows.net/",
            index_name=indexName,
            credential=credentials)
    # batch = []
    # for s in sections:
    #     batch.append(s)
    # results = searchClient.upload_documents(documents=batch)
    # succeeded = sum([1 for r in results if r.succeeded])
    # logging.info(f"\tIndexed {len(results)} sections, {succeeded} succeeded")
    i = 0
    batch = []
    for s in sections:
        batch.append(s)
        i += 1
        if i % 1000 == 0:
            results = searchClient.index_documents(batch=batch)
            succeeded = sum([1 for r in results if r.succeeded])
            logging.info(f"\tIndexed {len(results)} sections, {succeeded} succeeded")
            batch = []

    if len(batch) > 0:
        results = searchClient.upload_documents(documents=batch)
        succeeded = sum([1 for r in results if r.succeeded])
        logging.info(f"\tIndexed {len(results)} sections, {succeeded} succeeded")

def performCogSearch(indexType, embeddingModelType, question, indexName, k, returnFields=["id", "content", "metadata"] ):
    authority = AzureAuthorityHosts.AZURE_PUBLIC_CLOUD
    credentials = ClientSecretCredential(TenantId, ClientId, ClientSecret, authority=authority)
    searchClient = SearchClient(endpoint=f"https://{SearchService}.search.windows.net/",
            index_name=indexName,
            credential=credentials)
    try:
        if indexType == "cogsearchvs":
            r = searchClient.search(  
                search_text=question,
                vector_queries=[VectorizedQuery(vector=generateEmbeddings(embeddingModelType, question), k_nearest_neighbors=k, fields="content_vector")],  
                select=returnFields,
                query_type="semantic", 
                semantic_configuration_name='mySemanticConfig', 
                query_caption="extractive", 
                query_answer="extractive",
                include_total_count=True,
                top=k
            )
        elif indexType == "cogsearch":
            #r = searchClient.search(question, filter=None, top=k)
            try:
                r = searchClient.search(question, 
                                    filter=None,
                                    query_type=QueryType.SEMANTIC, 
                                    query_speller="lexicon", 
                                    semantic_configuration_name="mySemanticConfig", 
                                    top=k, 
                                    query_caption="extractive|highlight-false")
            except Exception as e:
                 r = searchClient.search(question, 
                                filter=None,
                                query_type=QueryType.SEMANTIC, 
                                query_speller="lexicon", 
                                semantic_configuration_name="default", 
                                top=k, 
                                query_caption="extractive|highlight-false")
        return r
    except Exception as e:
        logging.info(e)

    return None

def performSummaryQaCogSearch(indexType, embeddingModelType, question, indexName, k, returnFields=["id", "content", "metadata"] ):
    authority = AzureAuthorityHosts.AZURE_PUBLIC_CLOUD
    credentials = ClientSecretCredential(TenantId, ClientId, ClientSecret, authority=authority)
    searchClient = SearchClient(endpoint=f"https://{SearchService}.search.windows.net/",
            index_name=indexName,
            credential=credentials)
    try:
        if indexType == "cogsearch" or indexType == "cogsearchvs":
            #r = searchClient.search(question, filter=None, top=k)
            try:
                r = searchClient.search(question, 
                                    filter=None,
                                    query_type=QueryType.SEMANTIC, 
                                    query_speller="lexicon", 
                                    semantic_configuration_name="mySemanticConfig", 
                                    top=k, 
                                    query_caption="extractive|highlight-false")
            except Exception as e:
                 r = searchClient.search(question, 
                                filter=None,
                                query_type=QueryType.SEMANTIC, 
                                query_speller="lexicon", 
                                semantic_configuration_name="default", 
                                top=k, 
                                query_caption="extractive|highlight-false")
        return r
    except Exception as e:
        logging.info(e)

    return None


@retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(6))
# Function to generate embeddings for title and content fields, also used for query embeddings
def generateKbEmbeddings(OpenAiEndPoint, OpenAiKey, OpenAiVersion, OpenAiApiKey, OpenAiEmbedding, embeddingModelType, text):
    if (embeddingModelType == 'azureopenai'):
        try:
            client = AzureOpenAI(
                        api_key = OpenAiKey,  
                        api_version = OpenAiVersion,
                        azure_endpoint = OpenAiEndPoint
                        )

            response = client.embeddings.create(
                input=text, model=OpenAiEmbedding)
            embeddings = response.data[0].embedding
        except Exception as e:
            logging.info(e)

    elif embeddingModelType == "openai":
        try:
            client = OpenAI(api_key=OpenAiApiKey)
            response = client.embeddings.create(
                    input=text, model="text-embedding-ada-002", api_key = OpenAiApiKey)
            embeddings = response.data[0].embedding
        except Exception as e:
            logging.info(e)
        
    return embeddings

def createKbSearchIndex(SearchService, indexName):
    credentials = ClientSecretCredential(TenantId, ClientId, ClientSecret)
    indexClient = SearchIndexClient(endpoint=f"https://{SearchService}.search.windows.net/",
            credential=credentials)
    if indexName not in indexClient.list_index_names():
        index = SearchIndex(
            name=indexName,
            fields=[
                        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
                        SearchableField(name="question", type=SearchFieldDataType.String,
                                        searchable=True, retrievable=True, analyzer_name="en.microsoft"),
                        SearchableField(name="indexType", type=SearchFieldDataType.String, searchable=True, retrievable=True, filterable=True, facetable=False),
                        SearchableField(name="indexName", type=SearchFieldDataType.String, searchable=True, retrievable=True, filterable=True, facetable=False),
                        SearchField(name="vectorQuestion", type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                                    searchable=True, vector_search_dimensions=1536, vector_search_profile_name="vectorConfig"),
                        SimpleField(name="answer", type=SearchFieldDataType.String),
            ],
            vector_search = VectorSearch(
                    algorithms=[
                        HnswAlgorithmConfiguration(
                            name="hnswConfig",
                            parameters=HnswParameters(  
                                m=4,  
                                ef_construction=400,  
                                ef_search=500,  
                                metric=VectorSearchAlgorithmMetric.COSINE,  
                            ),
                        )
                    ],  
                    profiles=[  
                        VectorSearchProfile(  
                            name="vectorConfig",  
                            algorithm_configuration_name="hnswConfig",  
                        ),
                    ],
            ),
            semantic_config = SemanticConfiguration(
                name="mySemanticConfig",
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="question"),
                    content_fields=[SemanticField(field_name="question")]
                )
            )
        )

        try:
            print(f"Creating {indexName} search index")
            indexClient.create_index(index)
        except Exception as e:
            print(e)
    else:
        print(f"Search index {indexName} already exists")

def performKbCogVectorSearch(embedValue, embedField, SearchService, indexType, indexName, kbIndexName, k, returnFields=["id", "content", "metadata"] ):
    credentials = ClientSecretCredential(TenantId, ClientId, ClientSecret)
    searchClient = SearchClient(endpoint=f"https://{SearchService}.search.windows.net",
        index_name=kbIndexName,
        credential=credentials)
    
    try:
        logging.info("Create Index for KB : " + str(kbIndexName))
        createKbSearchIndex(SearchService, kbIndexName)
        r = searchClient.search(  
            search_text="",
            filter="indexType eq '" + indexType + "' and indexName eq '" + indexName + "'",
            vector_queries=[VectorizedQuery(vector=embedValue, k_nearest_neighbors=k, fields=embedField)],  
            select=returnFields,
            semantic_configuration_name="mySemanticConfig",
            include_total_count=True
        )
        return r
    except Exception as e:
        logging.info(e)

    return None

def indexDocs(SearchService, indexName, docs):
    print("Total docs: " + str(len(docs)))
    authority = AzureAuthorityHosts.AZURE_PUBLIC_CLOUD
    credentials = ClientSecretCredential(TenantId, ClientId, ClientSecret, authority=authority)
    searchClient = SearchClient(endpoint=f"https://{SearchService}.search.windows.net/",
            index_name=indexName,
            credential=credentials)

    i = 0
    batch = []
    for s in docs:
        batch.append(s)
        i += 1
        if i % 1000 == 0:
            results = searchClient.upload_documents(documents=batch)
            succeeded = sum([1 for r in results if r.succeeded])
            print(f"\tIndexed {len(results)} sections, {succeeded} succeeded")
            batch = []

    if len(batch) > 0:
        results = searchClient.upload_documents(documents=batch)
        succeeded = sum([1 for r in results if r.succeeded])
        print(f"\tIndexed {len(results)} sections, {succeeded} succeeded")