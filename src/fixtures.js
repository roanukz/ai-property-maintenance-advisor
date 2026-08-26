// Recorded output from a live run on 2026-08-18. This page makes no API call.
// Regenerating: see DECISION-LOG.md, 'How the demo stays free'.
window.BRIEFCASE_FIXTURES = {
  "recorded": "2026-08-18",
  "note": "Verbatim output from a live run. No API call is made by this page.",
  "cases": [
    {
      "id": "flo",
      "label": "Hot tub, code on the panel",
      "plate": "demo-assets/plate-clear.jpg",
      "extraction": {
        "manufacturer": "SUNDANCE® SPAS",
        "model": "OPTIMA 880",
        "serial": "100915742",
        "manufacture_date": "06/2014",
        "confidence": {
          "manufacturer": "high",
          "model": "high",
          "serial": "high",
          "manufacture_date": "high"
        }
      },
      "symptom": "panel shows FLO",
      "brief": {
        "identity": {
          "manufacturer": "Sundance Spas",
          "model": "Optima 880",
          "serial": "",
          "manufacture_date": ""
        },
        "symptom": "panel shows FLO",
        "brief": {
          "status": "ok",
          "matched_identity": "Sundance Spas Optima — Sundance 880 Series family (880 Series documented as including Aspen, Maxxus, Optima, Cameo, Majesta, Altamar, Capri)",
          "observed_code": "FLO",
          "warranty_caution": {
            "text": "Manufacture date is not readable on the data plate, so unit age cannot be established. Spa/hot tub warranties are commonly limited or voided when the unit is placed in commercial or rental service; no fetched source documented this for this specific model, so the owner must verify against their own warranty document before assuming any coverage.",
            "source_index": null
          },
          "happened_before": null,
          "try_first": [
            {
              "step": "Confirm water is visibly moving in the spa before anything else",
              "detail": "Documented instruction: before testing anything, confirm that water is visibly moving in the spa, and never test the heater without confirmed water flow. If no water movement is visible, stop and report to the owner rather than energizing or testing the heater.",
              "safety_flag": true,
              "source_index": 2
            },
            {
              "step": "Remove the filter cartridge and see whether the FLO message clears",
              "detail": "Documented test for the FLO error: if the error disappears with the filter out, the filter is clogged; if it stays, the next documented items to check are the flow switch or the heater circulation pump (those checks are not owner steps).",
              "safety_flag": false,
              "source_index": 1
            }
          ],
          "candidates": [
            {
              "code": "FLO",
              "documented_meaning": "Codes such as FLO, FL, or three blinking dots all indicate insufficient water flow through the heater.",
              "documented_action": "Confirm water is visibly moving in the spa; do not test the heater without confirmed water flow. Documented next checks are the pressure/flow switch test and, if bypassing the switch clears the error, replacing the switch or cable; if not, the circuit board is the likely cause. These checks are service work, not owner steps.",
              "who": "technician",
              "why_shown": "This is the exact code reported as showing on the unit's panel (FLO), and it is documented as an insufficient-flow-through-heater indication for Sundance spas.",
              "source_index": 2,
              "confirmed": true
            }
          ],
          "warranty": {
            "age_statement": "Manufacture date is not readable and the serial is not readable, so no age can be calculated as of 2026-08-18. Age of unit: unknown.",
            "cautions": [
              {
                "text": "Hot tubs placed in short-term rental / commercial service are commonly excluded from residential warranty terms. No fetched source documented the warranty terms for this model, so this is a caution only, not a statement about coverage.",
                "source_index": null
              }
            ],
            "verify": [
              "Locate the original Sundance Spas warranty document or dealer purchase paperwork and read the commercial/rental use clause.",
              "Recover the serial number and manufacture date from the data plate (usually inside the equipment bay door) so date-of-manufacture can be established.",
              "Confirm coverage status directly with the selling Sundance dealer before any parts are ordered or replaced."
            ]
          },
          "no_reliable_answer": null,
          "sources": [
            {
              "title": "Complete Sundance® Spas 880 Series Error Code Troubleshooting Guide",
              "url": "https://www.spastore.com/blog/complete-sundance-spas-880-series-error-code-troubleshooting-guide/",
              "tier": "dealer"
            },
            {
              "title": "How to Fix the Sundance Spa \"FLO\" Error",
              "url": "https://www.spastore.com/blog/how-to-fix-the-sundance-spa-flo-error/",
              "tier": "dealer"
            },
            {
              "title": "Hot Tub & Spa Circuit Board Problems: Symptoms, Causes, and Fixes",
              "url": "https://www.spastore.com/blog/hot-tub-spa-circuit-board-problems-symptoms-causes-and-fixes/",
              "tier": "dealer"
            }
          ]
        },
        "share": "549892815cb6.html",
        "usage": {
          "input_tokens": 64749,
          "output_tokens": 3476,
          "web_searches": 8
        }
      }
    },
    {
      "id": "blurry",
      "label": "A photo too poor to read",
      "plate": "demo-assets/plate-blurry.jpg",
      "extraction": {
        "manufacturer": "SUNDANCE SPAS",
        "model": null,
        "serial": null,
        "manufacture_date": null,
        "confidence": {
          "manufacturer": "low",
          "model": "unreadable",
          "serial": "unreadable",
          "manufacture_date": "unreadable"
        }
      },
      "symptom": "not heating",
      "brief": null,
      "stops_here": "The model number came back empty rather than guessed, so nothing is searched. The flow waits for the owner to type it in. A tool that filled this in from the visible brand and the shape of the plate would be right often enough to be trusted, and wrong exactly when it costs a truck roll."
    },
    {
      "id": "notheating",
      "label": "Hot tub, vague symptom",
      "plate": "demo-assets/plate-clear.jpg",
      "extraction": {
        "manufacturer": "SUNDANCE® SPAS",
        "model": "OPTIMA 880",
        "serial": "100915742",
        "manufacture_date": "06/2014",
        "confidence": {
          "manufacturer": "high",
          "model": "high",
          "serial": "high",
          "manufacture_date": "high"
        }
      },
      "symptom": "not heating",
      "brief": {
        "identity": {
          "manufacturer": "Sundance Spas",
          "model": "Optima 880",
          "serial": "",
          "manufacture_date": ""
        },
        "symptom": "not heating",
        "brief": {
          "status": "ok",
          "matched_identity": "Sundance Spas Optima 880 — documented as part of the Sundance 880 Series family (Aspen, Maxxus, Optima, Cameo, Majesta, Altamar, Capri)",
          "observed_code": null,
          "warranty_caution": {
            "text": "Sundance warranty material states coverage generally applies to original residential owners purchasing through an authorized dealer, and that commercial or short-term rental use may have reduced terms or be excluded entirely. Owner must verify against their own warranty document.",
            "source_index": 5
          },
          "happened_before": null,
          "try_first": [
            {
              "step": "Confirm the hot tub's house circuit breaker is ON",
              "detail": "The manual's start-up sequence states that when power is turned on to the hot tub at the home's circuit breaker, the heater and filter/circulation pump automatically activate. Only look at / switch the breaker itself — do not open the spa equipment bay or any electrical panel.",
              "safety_flag": true,
              "source_index": 1
            },
            {
              "step": "Look at the cabinet indicator light before touching anything else",
              "detail": "The 880 Series has an external indicator light on the front cabinet panel: solid white indicates normal operation, solid red indicates a critical error that requires immediate attention to prevent equipment damage. Report the color to the owner.",
              "safety_flag": true,
              "source_index": 2
            },
            {
              "step": "Check the water level is at the correct fill line",
              "detail": "Dealer troubleshooting guidance lists water level as the first check; an improper water level can cause various operational problems.",
              "safety_flag": false,
              "source_index": 3
            },
            {
              "step": "Remove and inspect the filter cartridge, then run the spa briefly with the filter out",
              "detail": "Dealer guidance: remove the filter and check for visible debris, clogs or trapped air; run the spa a few minutes without the filter installed — if the problem resolves, the filter is likely the culprit, and it should then be thoroughly cleaned or replaced.",
              "safety_flag": false,
              "source_index": 3
            },
            {
              "step": "Raise the temperature setpoint with the filter out and see whether the heater engages",
              "detail": "Q&A guidance for a Sundance spa showing FLO/COOL: leave the spa filter out, start the spa, turn up the temperature and see if the heater will engage and start.",
              "safety_flag": false,
              "source_index": 4
            },
            {
              "step": "If codes are latched, power down at the breaker for 10 minutes and restart",
              "detail": "Q&A guidance: turning the hot tub breaker off and leaving it off for 10 minutes erases the stored codes completely, then restart. Handle only the breaker switch — no wiring work.",
              "safety_flag": true,
              "source_index": 4
            }
          ],
          "candidates": [
            {
              "code": "FLO",
              "documented_meaning": "A flow error. Q&A guidance for Sundance spas attributes it to the filter not letting enough water through to the pump — even a cleaned cartridge whose fabric has failed can restrict flow.",
              "documented_action": "Removing or replacing the filter can restore proper circulation and clear this error; a new cartridge is described as solving the issue where cleaning did not.",
              "who": "anyone",
              "why_shown": "Surfaced because the reported symptom is 'not heating'; this source ties a FLO flow error directly to the spa not heating.",
              "source_index": 4,
              "confirmed": false
            },
            {
              "code": "COOL",
              "documented_meaning": "Per Q&A guidance, COOL notes that the water temperature is more than 15°F below the set temperature; the source states it does not by itself mean the heater is not working, and that it clears once the heater brings water within 15°F of setpoint. The same source notes COOL also comes up alongside a flow issue.",
              "documented_action": "Inspect the temperature probe and heater for damage or loose connections; where it accompanies a flow issue, address the filter/flow restriction.",
              "who": "technician",
              "why_shown": "COOL is a display message associated with water below setpoint, i.e. the 'not heating' complaint.",
              "source_index": 4,
              "confirmed": false
            },
            {
              "code": "COOL / ICE at start-up",
              "documented_meaning": "The owner's manual start-up section notes the control panel LCD may flash water temperature and 'COOL' or 'ICE' after power-on, alongside the automatic activation of heater and filter/circulation pump.",
              "documented_action": "Follow the manual's start-up sequence, including setting the hot tub to heat to warm water to a comfortable temperature.",
              "who": "anyone",
              "why_shown": "Applies if the spa was recently refilled or repowered and is simply still coming up to temperature.",
              "source_index": 1,
              "confirmed": false
            },
            {
              "code": null,
              "documented_meaning": "Freeze-protection behavior: the manual states that if the hot tub water is more than -7°C cooler than the temperature setpoint, jets pump #1 and the heater will automatically activate to provide freeze protection, and the tub stays in that mode until the water temperature recovers.",
              "documented_action": "Documented manual behavior; the same troubleshooting section directs the reader to a Sundance Spas dealer or qualified service technician for conditions it covers.",
              "who": "technician",
              "why_shown": "Surfaced because a cold-water / low-temperature display state can be normal protective behavior rather than a heater fault.",
              "source_index": 0,
              "confirmed": false
            },
            {
              "code": "FLO with circulation pump not running / no voltage at heater relay",
              "documented_meaning": "Q&A guidance for an 880 Cameo with a persistent FLO error and no circulation pump: verify pressure switch wiring and connections, check circulation pump continuity and impeller for blockages; no voltage at the heater relay is described as normal when the system detects a fault preventing heater operation.",
              "documented_action": "Inspect control board outputs and test relay coil resistance — electrical testing work.",
              "who": "technician",
              "why_shown": "Surfaced only if the filter/flow steps above do not restore heating and the circulation pump is not running.",
              "source_index": 6,
              "confirmed": false
            },
            {
              "code": null,
              "documented_meaning": "Solid red cabinet indicator light: a critical error is detected and the spa requires immediate attention to prevent equipment damage. These codes typically deactivate the heater or pumps for safety.",
              "documented_action": "Address the displayed error code; the source frames red-light codes as heater/pump-disabling protective states.",
              "who": "technician",
              "why_shown": "Directly relevant to 'not heating' because red-light critical codes are documented as deactivating the heater.",
              "source_index": 2,
              "confirmed": false
            }
          ],
          "warranty": {
            "age_statement": "Manufacture date is not readable on the data plate, so no age can be calculated as of 2026-08-18. Age math cannot be stated until the data plate or serial number is legible.",
            "cautions": [
              {
                "text": "Sundance warranty material states these warranties generally apply to original residential owners purchasing through an authorized Sundance dealer, and that commercial or short-term rental use may have reduced terms or be excluded entirely. This is a short-term rental property, so this caution applies directly.",
                "source_index": 5
              },
              {
                "text": "The same source states that most manufacturers, including Sundance, require proper water chemistry maintenance, and damage caused by improper water balance — corrosion, scale buildup, discolored shell — is typically excluded from coverage; it advises keeping water test records.",
                "source_index": 5
              },
              {
                "text": "The same source notes warranty transferability varies, that some warranties are non-transferable and protect only the original purchaser, and advises checking the specific warranty PDF for transfer terms before assuming coverage moves with the tub.",
                "source_index": 5
              }
            ],
            "verify": [
              "Locate the original purchase document and the warranty certificate that shipped with this specific Optima 880 and read the residential-use and commercial/rental clauses verbatim.",
              "Confirm whether the spa was bought new from an authorized Sundance dealer and by the current owner, or acquired second-hand.",
              "Recover the serial number / manufacture date from the data plate so the unit's age can be established.",
              "Confirm with the authorized dealer, in writing, how short-term rental use is treated for this unit before any paid repair is authorized."
            ]
          },
          "no_reliable_answer": null,
          "sources": [
            {
              "title": "Sundance Spas 880 Series Owner's Manual — Troubleshooting / Display Messages (p.80)",
              "url": "https://www.manualslib.com/manual/670390/Sundance-Spas-880-Series.html?page=80",
              "tier": "manufacturer"
            },
            {
              "title": "Sundance 2005+ 880 Series Owner's Manual (Revision C), PDF hosted by The Spa Works",
              "url": "https://www.thespaworks.com/sites/default/files/2005_sundance_880_series_owners_manual.pdf",
              "tier": "manufacturer"
            },
            {
              "title": "Complete Sundance Spas 880 Series Error Code Troubleshooting Guide — SpaStore.com",
              "url": "https://www.spastore.com/blog/complete-sundance-spas-880-series-error-code-troubleshooting-guide/",
              "tier": "dealer"
            },
            {
              "title": "Troubleshooting Your Sundance Spa: A Step-by-Step Guide — SpaStore.com",
              "url": "https://www.spastore.com/blog/troubleshooting-your-sundance-spa-a-stepbystep-guide/",
              "tier": "dealer"
            },
            {
              "title": "Sundance Spa 'Flo' & 'Cool' Errors: Troubleshooting Guide — JustAnswer",
              "url": "https://www.justanswer.com/pool-and-spa/lc5g1-sundance-spa-shows-flo-cool-errors-when-remove.html",
              "tier": "forum"
            },
            {
              "title": "What Hot Tub Has The Best Warranty — Sundance Spas",
              "url": "https://www.sundancespas.com/en-us/What-Hot-Tub-Has-The-Best-Warranty.html",
              "tier": "manufacturer"
            },
            {
              "title": "Sundance 880 Cameo FLO Error: Expert Solutions & Troubleshooting — JustAnswer",
              "url": "https://www.justanswer.com/pool-and-spa/nq7zy-sundance-880-cameo-flo-erro.html",
              "tier": "forum"
            }
          ]
        },
        "share": "db572cf7b81b.html",
        "usage": {
          "input_tokens": 90838,
          "output_tokens": 6936,
          "web_searches": 8
        }
      }
    },
    {
      "id": "hvac",
      "label": "HVAC, different category",
      "plate": "demo-assets/plate-clear.jpg",
      "extraction": null,
      "symptom": "AC not cooling upstairs",
      "brief": {
        "identity": {
          "manufacturer": "Trane",
          "model": "XR16 (4TTR6036)",
          "serial": "",
          "manufacture_date": ""
        },
        "symptom": "AC not cooling upstairs",
        "brief": {
          "status": "ok",
          "matched_identity": "Trane split-system air conditioner, 4TT-series outdoor unit family (guidance fetched covers Trane residential ACs generally and 4TT units specifically; no 4TTR6036-specific manual or error-code table was fetched in this session)",
          "observed_code": null,
          "warranty_caution": {
            "text": "No warranty document for this unit was fetched. Residential HVAC warranties commonly require registration within a set window after installation and commonly limit or exclude coverage when equipment is used in a commercial or rental application — the owner must verify this against their own Trane registration certificate and warranty document rather than relying on this brief.",
            "source_index": null
          },
          "happened_before": null,
          "try_first": [
            {
              "step": "Kill power before touching filters or any panel",
              "detail": "Trane's own troubleshooting guidance for an AC that is not cooling: \"Set your AC to 'OFF' and turn off the breakers that supply it with power\" before cleaning or replacing filters. Do this before opening any filter door or approaching the outdoor unit.",
              "safety_flag": true,
              "source_index": 0
            },
            {
              "step": "Confirm the thermostat is in cooling mode and set below room temperature",
              "detail": "Trane: check the thermostat setting and make sure it is set to cool; if it is set to cool, confirm the temperature setting is at the desired temperature. If it is not set to cool, switch to cooling operation.",
              "safety_flag": false,
              "source_index": 0
            },
            {
              "step": "Check for a schedule or energy-saving mode that is delaying cooling",
              "detail": "Documented check: confirm that no one changed the schedule or turned on energy-saving modes that delay cooling. Relevant at a rental where guests or a prior cleaner may have altered settings.",
              "safety_flag": false,
              "source_index": 1
            },
            {
              "step": "Replace dead thermostat batteries",
              "detail": "Documented remedy for a system that will not run/cool: turn on the thermostat or replace the batteries.",
              "safety_flag": false,
              "source_index": 2
            },
            {
              "step": "Clean or replace the air filter",
              "detail": "With power off at the breaker, clean or replace the filters. Consult a local Trane Comfort Specialist if you are unsure where the filters are located.",
              "safety_flag": false,
              "source_index": 0
            },
            {
              "step": "Clear debris from the outdoor condenser coil",
              "detail": "Documented remedy when the outdoor cooling coil is clogged or blocked: clean up the debris in the outside cooling coil. Keep power off at the breaker while doing this.",
              "safety_flag": true,
              "source_index": 2
            },
            {
              "step": "Check whether the circuit breaker is tripped",
              "detail": "Documented remedy: reset the circuit breaker. If it trips again after reset, stop and leave it off for a technician.",
              "safety_flag": true,
              "source_index": 2
            }
          ],
          "candidates": [
            {
              "code": null,
              "documented_meaning": "Clogged or dirty air filter restricting airflow. A clogged filter restricts airflow, causing the evaporator coil to freeze and the system to lose cooling capacity; the AC may run constantly but barely cool.",
              "documented_action": "Turn off the system at the thermostat, then clean or replace the filter.",
              "who": "anyone",
              "why_shown": "Listed as one of the most common documented causes of a Trane central AC not cooling, which is the reported symptom.",
              "source_index": 1,
              "confirmed": false
            },
            {
              "code": null,
              "documented_meaning": "Blocked or clogged condenser coil at the outdoor unit — when operating correctly the condenser fan draws air through the condenser coil to pull heat energy out of the home.",
              "documented_action": "Clean up the debris in the outside cooling coil.",
              "who": "anyone",
              "why_shown": "Trane lists a blocked/clogged condenser coil among documented causes of an AC running but not lowering indoor temperature.",
              "source_index": 0,
              "confirmed": false
            },
            {
              "code": null,
              "documented_meaning": "Indoor air handler coil clogged or blocked with dust buildup.",
              "documented_action": "Wipe away any buildup of dust on the coil or change the air filter.",
              "who": "technician",
              "why_shown": "Documented cause of no-cool on Trane air conditioners; accessing the indoor coil is beyond a cleaner's scope, so routed to a technician.",
              "source_index": 2,
              "confirmed": false
            },
            {
              "code": null,
              "documented_meaning": "Refrigerant low or leaking.",
              "documented_action": "Contact an authorized service technician.",
              "who": "technician",
              "why_shown": "Trane lists leaking or low refrigerant among the common causes when an AC is not cooling the house.",
              "source_index": 2,
              "confirmed": false
            },
            {
              "code": null,
              "documented_meaning": "Failed capacitor, or a failed motor/compressor — indicated when the outdoor unit is not running or is unusually loud.",
              "documented_action": "These require professional diagnosis and repair; schedule AC repair service.",
              "who": "technician",
              "why_shown": "A bad capacitor is listed among the most common reasons a Trane central AC is not cooling; surfaced by the no-cool symptom.",
              "source_index": 1,
              "confirmed": false
            },
            {
              "code": null,
              "documented_meaning": "Thermostat controls set incorrectly, or thermostat electronics/wiring failing (no response after fresh batteries and correct settings).",
              "documented_action": "Reset the thermostat; if it still does not respond after fresh batteries and proper settings, schedule AC repair service.",
              "who": "anyone",
              "why_shown": "Thermostat problems are listed among the most common documented causes of a Trane AC not cooling.",
              "source_index": 1,
              "confirmed": false
            }
          ],
          "warranty": {
            "age_statement": "Age cannot be stated. The serial number and manufacture date on the data plate are not readable, so no manufacture-date-to-today (2026-08-18) age calculation is possible for this unit.",
            "cautions": [
              {
                "text": "Residential HVAC warranties commonly carry registration deadlines and commonly restrict coverage for commercial or short-term-rental use. No warranty document was fetched in this session, so nothing here should be read as a statement that any repair is or is not covered.",
                "source_index": null
              }
            ],
            "verify": [
              "Locate the outdoor unit's full serial number (data plate on the condenser cabinet, often behind or beside the service panel) and photograph it — the manufacture date is encoded there.",
              "Retrieve the original Trane warranty registration certificate and the installing dealer's invoice with the install date.",
              "Read the warranty document's own terms on commercial / non-owner-occupied / rental use before authorizing any paid repair.",
              "Confirm the full model number on the data plate (e.g. 4TTR6036xxxxxx) so a technician can pull the correct model-specific service literature."
            ]
          },
          "no_reliable_answer": null,
          "sources": [
            {
              "title": "Air Conditioner Troubleshooting: Why Your AC Is Not Cooling — Trane Residential",
              "url": "https://www.trane.com/residential/en/resources/troubleshooting/air-conditioners/",
              "tier": "manufacturer"
            },
            {
              "title": "Trane Air Conditioner Not Cooling: Common Causes & Easy Troubleshooting — Sears Home Services",
              "url": "https://www.searshomeservices.com/symptom/trane-central-air-not-cooling",
              "tier": "dealer"
            },
            {
              "title": "Trane Air Conditioner Troubleshooting — Parts Town Resource Center (states it applies to Trane 4TT units)",
              "url": "https://www.partstown.com/cm/resource-center/guides/gd2/trane-air-conditioner-troubleshooting",
              "tier": "dealer"
            }
          ]
        },
        "share": "c45b900046bf.html",
        "usage": {
          "input_tokens": 54552,
          "output_tokens": 5293,
          "web_searches": 8
        }
      }
    },
    {
      "id": "unknown",
      "label": "A model that does not exist",
      "plate": "demo-assets/plate-blurry.jpg",
      "extraction": null,
      "symptom": "not heating",
      "brief": {
        "identity": {
          "manufacturer": "Aquarest",
          "model": "ZX-9000 Pro",
          "serial": "",
          "manufacture_date": ""
        },
        "symptom": "not heating",
        "brief": {
          "status": "no_reliable_answer",
          "matched_identity": null,
          "observed_code": null,
          "warranty_caution": null,
          "happened_before": null,
          "try_first": [],
          "candidates": [],
          "warranty": null,
          "no_reliable_answer": {
            "searched": [
              "Aquarest ZX-9000 Pro manual",
              "Aquarest ZX-9000 spa",
              "Aquarest spa ZX-9000 Pro not heating error codes",
              "\"ZX-9000\" Aquarest",
              "\"ZX-9000 Pro\" hot tub",
              "AquaRest spa model list / lineup (Select, Elite, AR- series)",
              "aquarestspas.com owners manuals model lineup AR-300 AR-500 Day Dream"
            ],
            "found": [
              "Official AquaRest owner's manuals exist for the UHS and HU2 spa control platforms (PN 378100 series) hosted on aquarestspas.com, but none reference a ZX-9000 or ZX series.",
              "The manufacturer's own owners/support and showroom pages list AquaRest's product lineup under names such as AR-300, AR-400, AR-500, AR-600, Select 300, Elite 500/600, and Day Dream — no ZX-prefixed model appears.",
              "ManualsLib's AquaRest Spas brand page indexes owner's manuals only for AR-300 TRI, AR-400 X4H, AR-500 X5H, and AR-600 EZB.",
              "Retailer listings (Home Depot, Wayfair, Amazon, AquaRest Outlet) for AquaRest spas likewise show only AR-/Select/Elite naming.",
              "Every search for the literal string \"ZX-9000\" returned unrelated products (Adidas ZX 9000 sneakers, Master Spas LSX 900, Pinnacle Spas LX 9000) — no hot tub by AquaRest with this designation.",
              "General AquaRest 'not heating' troubleshooting content exists on third-party Q&A and blog sites (JustAnswer, tpa10.com, troubleshootinglab.com), but none is tied to a ZX-9000 Pro or any documented family containing it."
            ],
            "why_insufficient": "No manufacturer, dealer, or retailer source found in this session documents an AquaRest 'ZX-9000 Pro', and the designation does not fit AquaRest's documented model naming (AR-###, Select, Elite, Day Dream) or its documented control-pack families (UHS, HU2). Because the model cannot be matched to any documented family, there is no source-backed error-code table, documented cause list, or documented action for a 'not heating' symptom on this unit. Applying generic hot-tub or generic AquaRest troubleshooting here would be a guess, not documentation. Next step for the owner: re-read the data plate (the plate on AquaRest spas typically shows a model code such as X5H-UHS-BB or AR-500, plus the control pack designation) and photograph it, including the topside control panel and any code currently displayed; with that exact model/pack code the correct manual and error-code table can be identified."
          },
          "sources": [
            {
              "title": "AquaRest Spas — Owners (support and manuals hub)",
              "url": "https://aquarestspas.com/owners/",
              "tier": "manufacturer"
            },
            {
              "title": "AquaRest HU2 Spa Owner's Manual, PN 378100-2 Rev S (Domestic)",
              "url": "https://aquarestspas.com/wp-content/uploads/2019/02/AR-OWNERS-MANUAL-DOMESTIC-ENGLISH-HU2-378100-2-REV-S-.pdf",
              "tier": "manufacturer"
            },
            {
              "title": "AquaRest UHS Spa Owner's Manual, PN 378100-1 Rev O (Domestic)",
              "url": "https://aquarestspas.com/wp-content/uploads/2016/12/AR-2017-OWNERS-MANUAL-DOMESTIC-ENGLISH-UHS-378100-1-REV-O-.pdf",
              "tier": "manufacturer"
            },
            {
              "title": "AquaRest Spas — Spa Showroom (current model lineup)",
              "url": "https://aquarestspas.com/spa-showroom/",
              "tier": "manufacturer"
            },
            {
              "title": "AquaRest Spas User Manuals Download — brand index (AR-300 TRI, AR-400 X4H, AR-500 X5H, AR-600 EZB)",
              "url": "https://www.manualslib.com/brand/aquarest-spas/",
              "tier": "dealer"
            },
            {
              "title": "AquaRest Spas — Hot Tubs at The Home Depot (retail model listings)",
              "url": "https://www.homedepot.com/b/Outdoors-Home-Spas-Hot-Tubs/AquaRest-Spas/N-5yc1vZcfz2Ze48",
              "tier": "dealer"
            }
          ]
        },
        "share": "a601fd524225.html",
        "usage": {
          "input_tokens": 41708,
          "output_tokens": 2698,
          "web_searches": 7
        }
      }
    }
  ]
};
