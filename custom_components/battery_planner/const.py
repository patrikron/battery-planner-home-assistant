"""Constants for Battery Planner."""

DOMAIN = "battery_planner"
NAME = "Batteriplanerare"
VERSION = "0.7.0"

CONF_PRICE_ENTITY = "price_entity"
CONF_SELL_ENTITY = "sell_entity"
CONF_IMPORT_ENTITY = "grid_import_entity"
CONF_EXPORT_ENTITY = "grid_export_entity"
CONF_SOC_ENTITY = "soc_entity"
CONF_CAPACITY = "capacity_kwh"
CONF_MIN_SOC = "min_soc_pct"
CONF_MAX_CHARGE = "max_charge_kw"
CONF_MAX_DISCHARGE = "max_discharge_kw"
CONF_EFFICIENCY = "roundtrip_efficiency_pct"
CONF_FUSE_A = "main_fuse_a"
CONF_ALLOW_EXPORT = "allow_export"
CONF_WEAR_COST = "wear_cost"
CONF_SELL_MARGIN = "sell_margin"
CONF_SELL_BUFFER = "sell_buffer_pct"
CONF_MODE = "mode"
CONF_HEATING_ENTITIES = "heating_energy_entities"
CONF_OUTDOOR_ENTITY = "outdoor_temp_entity"
CONF_WEATHER_ENTITY = "weather_entity"
CONF_FIRE_ENTITY = "fire_temp_entity"
CONF_FIRE_THRESHOLD = "fire_threshold"
CONF_VVB_PLAN_ENTITY = "vvb_plan_entity"
CONF_VVB_ENERGY_ENTITY = "vvb_energy_entity"
CONF_CAR_ENERGY_ENTITY = "car_energy_entity"
CONF_CAR_SOC_ENTITY = "car_soc_entity"
CONF_CAR_TARGET_ENTITY = "car_target_entity"
CONF_CAR_PLUG_ENTITY = "car_plug_entity"
CONF_CAR_CAPACITY = "car_capacity_kwh"
CONF_CAR_POWER = "car_power_kw"
CONF_CAR_READY_BY = "car_ready_by"
CONF_PV_ENTITY = "pv_energy_entity"
CONF_USE_SOLAR_FORECAST = "use_solar_forecast"
CONF_MAX_SOC = "max_soc_pct"
CONF_BATTERY_PRICE = "battery_price"
CONF_LIFETIME_MWH = "lifetime_mwh"
CONF_SPARE_HEAT_KW = "spare_heat_kw"
CONF_SPARE_BUFFER = "spare_buffer_pct"

MODE_SHADOW = "shadow"          # plans and simulates, controls nothing
MODES = [MODE_SHADOW]

DEFAULTS = {
    CONF_CAPACITY: 23.04,
    CONF_MIN_SOC: 10,
    CONF_MAX_CHARGE: 10.0,
    CONF_MAX_DISCHARGE: 10.0,
    CONF_EFFICIENCY: 90,
    CONF_FUSE_A: 16,
    CONF_ALLOW_EXPORT: False,
    CONF_WEAR_COST: 0.05,
    CONF_SELL_MARGIN: 0.10,
    CONF_SELL_BUFFER: 20,
    CONF_FIRE_THRESHOLD: 40,
    CONF_CAR_CAPACITY: 77.4,
    CONF_CAR_POWER: 11.0,
    CONF_CAR_READY_BY: "07:00:00",
    CONF_USE_SOLAR_FORECAST: True,
    CONF_MAX_SOC: 100,
    CONF_BATTERY_PRICE: 0,
    CONF_LIFETIME_MWH: 0,
    CONF_SPARE_HEAT_KW: 0.0,
    CONF_SPARE_BUFFER: 20,
}

HISTORY_DAYS = 29
FORECAST_WEEKS = 4
SOLAR_WEEKS = 1
STEP_KWH = 0.25
HIGH_SOC = 0.8           # above this the planner prefers to charge late
HOLD_COST = 0.002        # kr per kWh and hour kept above HIGH_SOC (tie-breaker only)
SPARE_OFF_HOURS = 0.5    # "Element får köra" stays on until the spare covers less than this many hours
STORAGE_VERSION = 1
