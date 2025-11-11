# a fastapi based API to read relay temperatures
from fastapi import FastAPI, APIRouter

from ..inventoryrelays import InventoryRelays

inventory_relays = InventoryRelays.load()

app = FastAPI()

# define prefix /api/v1
api_v1 = APIRouter(prefix="/api/v1")

@api_v1.post("/relays/temperatures")
async def get_relays_temperatures(
    duration: str = None, resample_period: str = None):
    """
    Retrieve relay temperatures with optional filtering by duration and resample period.
    """
    # both parameters are optional and can be passed in a JSON body
    pd_duration = pd.Timedelta(duration) if duration else None
    pd_resample_period = pd.Timedelta(resample_period) if resample_period else None
    df = inventory_relays.load_past_data(
        duration=pd_duration,
        resample_period=pd_resample_period
    )
    return df.to_csv()

app.include_router(api_v1)
def run_relays_api_service():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
