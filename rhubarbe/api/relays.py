# a fastapi based API to read relay temperatures
from fastapi import FastAPI, APIRouter
from pydantic import BaseModel
from typing import Optional

from ..inventoryrelays import InventoryRelays

inventory_relays = InventoryRelays.load()

app = FastAPI()

# define prefix /api/v1
api_v1 = APIRouter(prefix="/api/v1")


class RelayTemperatureQuery(BaseModel):
    duration: Optional[str] = None
    resample_period: Optional[str] = None


@api_v1.post("/relays/temperatures")
async def get_relays_temperatures(params: RelayTemperatureQuery):
    """
    Retrieve relay temperatures with optional filtering by duration and resample period.
    """
    # both parameters are optional and can be passed in a JSON body
    df = inventory_relays.load_past_data(
        duration=params.duration,
        resample_period=params.resample_period
    ).reset_index()
    return df.to_json(orient='records')

app.include_router(api_v1)
def run_relays_api_service():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
