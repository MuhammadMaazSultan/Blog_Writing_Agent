from langgraph.types import Send
from langgraph.graph import StateGraph, START, END
from langchain_core.messages import SystemMessage, HumanMessage
from langchain.output_parsers import PydanticOutputParser
from langchain_ollama import ChatOllama
from typing import TypedDict, List, Literal, Annotated
from pydantic import BaseModel, Field
import operator
from pathlib import Path
from langchain_community.tools.tavily_search import TavilySearchResults
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

load_dotenv()

#---- model ----
model = ChatOllama(model='llama3.1' , temperature = 0.5)

#---- schemas ----
class Task(BaseModel):
    tone : str = Field(..., description = "In which tone we have to write this blog part")
    words : int = Field(..., description = "how many words will it contain")
    title : str = Field(..., description = "the title derived for blog writing in plan by breaking down")
    brief : str = Field(..., description = "what to cover")

class Plan(BaseModel):
    blog_title : str
    task : List[Task] = Field(..., description="list of tasks to complete")

class RouterDecision(BaseModel):
    needs_research : bool = Field(...,description="True if web search is required to gather up-to-date or specific facts.",)
    queries : List[str] = Field(default_factory=list, description="3-10 high-signal specific search queries if search is needed.",)
    
class BlogState(TypedDict):
    topic : str
    plan : Plan
    needs_research : bool
    evidences : Annotated[List[dict], operator.add]
    queries : List[str] #--v1
    sections : Annotated[List[str], operator.add]
    Blog : str


#---- utility functions ----
def search(query, num_responses):
    search_tool = TavilySearchResults(max_results=num_responses)
    response = search_tool.invoke({'query':query})
    normalized :List[dict] = []
    for i in response:
        normalized.append(
            {
                "title":i['title'] or "",
                "url":i['url'] or "",
                "content":i['content'] or ""
            }
        )

    return normalized

#---- defining nodes ----
def router(state: BlogState):
    topic = state['topic']
    model_with_router = model.with_structured_output(RouterDecision)

    prompt = """You are a routing module 
    decide if the web search is needed to get the results or not
    
    If needs_research=true:
    - Output 3–10 high-signal queries.
    - Queries should be scoped and specific (avoid generic queries like just "AI" or "LLM").
    - If user asked for "last week/this week/latest", reflect that constraint IN THE QUERIES."""
    response = model_with_router.invoke([SystemMessage(prompt),
                  HumanMessage(content=f'topic : {topic}')])
    return {'needs_research':response.needs_research, 'queries': response.queries}

def decide_to_route(state:BlogState):
    return 'research' if state['needs_research'] else 'orchestrator'

def research(state:BlogState):
    queries = state['queries']
    responses : List[dict] = []
    with ThreadPoolExecutor() as concurrent_exe:
        results = [concurrent_exe.submit(search, q, 1) for q in queries] 
        for result in results:
            responses.extend(result.result())
    return {'evidences': responses}
    

def planner(state: BlogState):
    topic = state['topic']
    web_searches = state['evidences']
    prompt = " Create a blog plan with 5-7 sections on the following topic and also consider the searches that we have explicitly searched on the browser"
    planner_model = model.with_structured_output(Plan)
    response = planner_model.invoke([SystemMessage(prompt),  HumanMessage(content = f'topic : {topic}, web_searched: {web_searches}')])
    return {'plan':response}

def fanout(state:BlogState):
    return [ Send('worker' , {'task':task, 'topic':state['topic'], 'evidences': state['evidences']}) for task in state['plan'].task]

def worker(payload: dict) -> dict:
    topic = payload['topic']
    task = payload['task']
    evidences = payload['evidences']
    prompt = [SystemMessage("write one clean markdown section"),
             HumanMessage(content=f"topic: {topic}\ntitle: {task.title}\ntone: {task.tone}\nwords: {task.words}\nbrief: {task.brief}\n web_searched : {evidences}")]
    response = model.invoke(prompt).content.strip()
    return {'sections':[response]}

def reducer(state: BlogState) -> dict:
    topic = state['topic']
    sections = state['sections']
    body = '\n\n'.join(sections).strip()

    final_md = f'# {topic} \n\n {body}'
    filename = topic.replace(' ', '_').lower() +'.md'
    output_path = Path(filename)
    output_path.write_text(final_md, encoding='utf-8')
    return {"Blog": final_md}

#---- defining graph ----
graph_builder = StateGraph(BlogState)

graph_builder.add_node('router', router)
graph_builder.add_node('researcher', research)
graph_builder.add_node('orchestrator', planner)
graph_builder.add_node('worker', worker)
graph_builder.add_node('reducer', reducer)

graph_builder.add_edge(START, 'router')
graph_builder.add_conditional_edges('router', decide_to_route, {'research':'researcher', 'orchestrator':'orchestrator'})
graph_builder.add_edge('researcher', 'orchestrator')
graph_builder.add_conditional_edges('orchestrator', fanout, ['worker'])
graph_builder.add_edge('worker', 'reducer')
graph_builder.add_edge('reducer', END)

graph = graph_builder.compile()

#---- invoking to get the required Blog ----
initial_state = {'topic':'Iran Israel War 2026'}
response = graph.invoke(initial_state)

print(response['Blog'])