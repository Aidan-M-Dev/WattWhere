# Data Sources: Optimal Data Centre Siting in Ireland

## Key Portals
- **data.gov.ie** — National open data aggregator (21,500+ datasets)
- **GeoHive / Tailte Éireann** — data-osi.opendata.arcgis.com (geospatial boundaries, mapping)
- **EPA Geoportal** — gis.epa.ie (environmental data, downloads & WMS/WFS)

Almost all Irish government spatial data uses **ITM projection (EPSG:2157)** — one reprojection step to WGS84 for web mapping.

---

## 1. Land Use / Land Cover
| Dataset | Provider | Format | Notes |
|---------|----------|--------|-------|
| National Land Cover Map 2018 | Tailte Éireann / EPA | WMS, ArcGIS REST | 36 classes, <0.1 ha — 250× more detailed than CORINE |
| CORINE Land Cover 2018 | EPA / Copernicus | Shapefile | Quickest to use — single national shapefile |
| URL: data.gov.ie/dataset/high-value-dataset-national-land-cover-2018 | | | |

## 2. Renewable Energy Potential
| Dataset | Provider | Format | Notes |
|---------|----------|--------|-------|
| Global Wind Atlas 3.0 | DTU / World Bank | GeoTIFF | 250m grid, 10–200m heights. globalwindatlas.info |
| Global Solar Atlas 2.0 | Solargis / World Bank | GeoTIFF | 250m GHI/DNI. globalsolaratlas.info |
| SEAI Wind Speed Shapefiles | SEAI / data.gov.ie | Shapefile (ITM) | 200m grid, multiple heights |
| SEAI Wind Farms Locations | SEAI / data.gov.ie | Shapefile | Existing wind farm point locations |
| URL: data.gov.ie/dataset/average-wind-speeds-2001-to-2010-50m-above-ground-level | | | |

## 3. Electricity Grid Infrastructure ⚠️ (weakest layer)
| Dataset | Provider | Format | Notes |
|---------|----------|--------|-------|
| **OpenStreetMap Power Infrastructure** | Geofabrik | Shapefile, PBF | **Best option** — filter for `power=*` tags. download.geofabrik.de |
| EirGrid Smart Grid Dashboard | EirGrid | JSON API | Real-time gen data. smartgriddashboard.com |
| EirGrid Transmission Map | EirGrid | PDF only ❌ | Visual reference only |
| Open Infrastructure Map | Community | Web viewer | openinframap.org — visualises OSM power data |

## 4. Environmental Sensitivity (Protected Areas)
| Dataset | Provider | Format | Notes |
|---------|----------|--------|-------|
| SAC, SPA, NHA, pNHA boundaries | NPWS | Shapefile (ITM), WFS | **Excellent** — direct download, updated Feb 2026 |
| URL: npws.ie/maps-and-data/designated-site-data/download-boundary-data | | | |
| These form your **exclusion zones** — auto-disqualify or heavily penalise any site within them. | | | |

## 5. Population Density
| Dataset | Provider | Format | Notes |
|---------|----------|--------|-------|
| Census 2022 Small Area Population Stats | CSO | CSV (16.6MB ZIP) | 18,919 Small Areas (~60–120 households each) |
| Small Area Boundaries 2022 | Tailte Éireann | Shapefile, GeoJSON | Join to SAPS CSV on Small Area code |
| URL (stats): cso.ie/en/census/census2022/census2022smallareapopulationstatistics/ | | | |
| URL (boundaries): data-osi.opendata.arcgis.com — search "Small Areas 2022" | | | |

## 6. Water Availability (for cooling)
| Dataset | Provider | Format | Notes |
|---------|----------|--------|-------|
| River Network & Lakes | EPA | Shapefile | 1:50,000 scale national. gis.epa.ie/GetData/Download |
| WFD Waterbodies (rivers, lakes, groundwater) | EPA | Shapefile | Individual waterbody polygons |
| OPW Hydrometric Stations (flow rates) | OPW | CSV + GeoJSON | 300+ stations, 15-min data. waterlevel.ie |
| Bedrock Aquifer Maps | GSI | Shapefile, ArcGIS REST | gsi.ie data-and-maps |

## 7. Climate / Temperature
| Dataset | Provider | Format | Notes |
|---------|----------|--------|-------|
| **Gridded Monthly Temp/Rainfall** | Met Éireann | Grid/raster | **1km × 1km** — ideal for cooling cost estimation |
| Weather Forecast API | Met Éireann | XML REST API | No API key needed. openaccess.pf.api.met.ie |
| MÉRA Reanalysis | Met Éireann | GRIB | 2.5km grid, hourly, 1981–2019. Request via mera@met.ie |
| URL: met.ie/climate/available-data |

## 8. Transport & Digital Connectivity
| Dataset | Provider | Format | Notes |
|---------|----------|--------|-------|
| OpenStreetMap (full road network) | Geofabrik | Shapefile, PBF | download.geofabrik.de — daily updates |
| TII National Roads | TII | Shapefile, KML | data.tii.ie |
| ComReg Open Data Map Hub | ComReg | ArcGIS Hub/REST | Broadband coverage. datamaps-comreg.hub.arcgis.com |
| INEX locations | PeeringDB | JSON API | peeringdb.com/ix/48 — Dublin & Cork IXPs |
| ⚠️ No public GIS dataset for fibre routes exists. ComReg coverage is the best proxy. | | | |

## 9. Planning & Zoning
| Dataset | Provider | Format | Notes |
|---------|----------|--------|-------|
| **Development Plan Zoning (GZT)** | DHLGH / MyPlan | Shapefile, GeoJSON, ArcGIS REST | **Key layer** — filter for Industrial/Enterprise zones |
| National Planning Applications | DHLGH | GeoJSON, ArcGIS REST | All applications since 2010, updated weekly |
| URL: data-housinggovie.opendata.arcgis.com — search "Development Plan Zoning" | | | |
| IDA Ireland Properties | IDA | Web only ❌ | ~92 industrial sites, no GIS download |

## 10. Flood & Natural Disaster Risk
| Dataset | Provider | Format | Notes |
|---------|----------|--------|-------|
| NIFM Flood Extents (Current + Future) | OPW | Shapefile | National flood zones. data.gov.ie search "NIFM" |
| Landslide Susceptibility Map | GSI | Shapefile, ArcGIS REST | data.gov.ie |
| Groundwater Flood Maps | GSI | Shapefile | Karst areas. opendata-geodata-gov-ie.hub.arcgis.com |
| OPW Flood Maps Viewer | OPW | WMS | floodinfo.ie |
| ⚠️ OPW flood data is CC BY-NC-ND (fine for hackathon, not commercial use). | | | |

---

## Suggested Layer Priority for Hackathon
1. **Base layer:** MyPlan GZT zoning (identify suitable zones)
2. **Exclusion zones:** NPWS protected areas + OPW flood extents
3. **Scoring layers:** Wind/solar potential, grid proximity (OSM), temperature (Met Éireann 1km), water proximity (EPA rivers), population density (CSO)

## Existing Reference Data
- **The Journal Investigates** data centre map — 89 operating, 11 under construction, 30+ with planning permission (investigates.thejournal.ie/data-centres)
- **DataCenterMap.com** — 110+ Dublin-area facilities with addresses
- **GitHub: Open Data in Ireland** — community directory (github.com/virtualarchitectures/Open-Data-in-Ireland)
