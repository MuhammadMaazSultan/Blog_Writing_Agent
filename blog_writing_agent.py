from langgraph.types import Send
from langgraph.graph import StateGraph, START, END
from langchain_core.messages import SystemMessage, HumanMessage
from langchain.prompts import PromptTemplate
from langchain.output_parsers import PydanticOutputParser
from langchain_ollama import ChatOllama
from typing import TypedDict, List, Literal, Annotated
from pydantic import BaseModel, Field
import operator
from pathlib import Path

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
    
class BlogState(TypedDict):
    topic : str
    plan : Plan
    sections : Annotated[List[str], operator.add]
    Blog : str

#---- defining nodes ----
def planner(state: BlogState):
    topic = state['topic']
    prompt = """ Create a blog plan with 5-7 sections on the following topic"""
    planner_model = model.with_structured_output(Plan)
    response = planner_model.invoke([SystemMessage(prompt),  HumanMessage(content = f'topic : {topic}')])
    return {'plan':response}

def fanout(state:BlogState):
    return [ Send('worker' , {'task':task, 'topic':state['topic']}) for task in state['plan'].task]

def worker(payload: dict) -> dict:
    topic = payload['topic']
    task = payload['task']
    prompt = [SystemMessage("write one clean markdown section"),
             HumanMessage(content=f"topic: {topic}\ntitle: {task.title}\ntone: {task.tone}\nwords: {task.words}\nbrief: {task.brief}")]
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

graph_builder.add_node('orchestrator', planner)
graph_builder.add_node('worker', worker)
graph_builder.add_node('reducer', reducer)

graph_builder.add_edge(START, 'orchestrator')
graph_builder.add_conditional_edges('orchestrator', fanout, ['worker'])
graph_builder.add_edge('worker', 'reducer')
graph_builder.add_edge('reducer', END)

graph = graph_builder.compile()

#---- invoking to get the required Blog ----
initial_state = {'topic':'Self Attention'}
response = graph.invoke(initial_state)

print(response['Blog'])