import time
import urllib.parse
import requests

from flask import Flask, jsonify, render_template, request


app = Flask(__name__)


# ============================================================
# OPEN FOOD FACTS
# ============================================================

OFF_HOST = "world.openfoodfacts.org"

# Replace this with your team's real project/contact information.
USER_AGENT = (
    "FoodLens/1.0 "
    "(student prototype; contact: your-email@example.com)"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
}


# ============================================================
# HELPER: SAFE NUMBER
# ============================================================

def number_or_none(value):
    """
    Convert a value to float where possible.
    Return None when the value is missing or invalid.
    """

    if value is None:
        return None

    try:
        number = float(value)

        if number != number:  # NaN check
            return None

        return number

    except (TypeError, ValueError):
        return None


def clean_number(value, decimals=2):
    """
    Return a rounded number or None.
    """

    if value is None:
        return None

    return round(float(value), decimals)


# ============================================================
# HELPER: OPEN FOOD FACTS REQUEST
# ============================================================

def off_get(url, params=None, retries=3):
    """
    Request data from Open Food Facts.

    Retries temporary 429 / 5xx errors.
    Does NOT retry genuine 404 product-not-found responses.
    """

    last_error = None

    for attempt in range(retries):

        try:
            response = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=15
            )

            # Product genuinely does not exist.
            if response.status_code == 404:
                return None, 404

            # Temporary / rate-limit errors.
            if response.status_code in (429, 500, 502, 503, 504):

                last_error = (
                    f"OpenFoodFacts temporary error "
                    f"{response.status_code}"
                )

                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue

                return None, response.status_code

            response.raise_for_status()

            return response.json(), response.status_code

        except requests.exceptions.Timeout:

            last_error = "OpenFoodFacts request timed out."

            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue

        except requests.exceptions.ConnectionError:

            last_error = "Could not connect to OpenFoodFacts."

            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue

        except requests.exceptions.RequestException as e:

            last_error = str(e)
            break

        except ValueError:

            last_error = "OpenFoodFacts returned invalid JSON."
            break

    return None, last_error


# ============================================================
# NUTRIENT COMPONENT SCORE
# ============================================================

def sugar_score(sugar):
    """
    FoodLens sugar component.

    Based on internationally recognized per-100g
    nutrient thresholds and WHO dietary guidance.

    NOTE:
    Open Food Facts generally provides TOTAL SUGARS.
    WHO's dietary recommendation specifically concerns
    FREE SUGARS, so this is a conservative FoodLens
    application score, not a WHO score.
    """

    if sugar is None:
        return None, "Unavailable"

    if sugar <= 5:
        return 10, "Low"

    if sugar <= 10:
        return 8, "Moderate"

    if sugar <= 15:
        return 6, "Moderate"

    if sugar <= 22.5:
        return 4, "High"

    if sugar <= 30:
        return 2, "High"

    return 1, "Very High"


def sodium_score(sodium_mg):
    """
    FoodLens sodium component.

    Sodium is standardized to mg per 100g.
    """

    if sodium_mg is None:
        return None, "Unavailable"

    if sodium_mg <= 120:
        return 10, "Low"

    if sodium_mg <= 300:
        return 8, "Low"

    if sodium_mg <= 500:
        return 6, "Moderate"

    if sodium_mg <= 800:
        return 4, "High"

    if sodium_mg <= 1200:
        return 2, "High"

    return 1, "Very High"


def fibre_score(fibre):
    """
    FoodLens fibre component.

    EU nutrient-claim benchmarks:
        3g/100g = source of fibre
        6g/100g = high fibre

    Intermediate values are FoodLens interpolation.
    """

    if fibre is None:
        return None, "Unavailable"

    if fibre < 1.5:
        return 2, "Very Low"

    if fibre < 3:
        return 5, "Low"

    if fibre < 4.5:
        return 7, "Good"

    if fibre < 6:
        return 8, "Good"

    return 10, "High"


def protein_score(protein, calories):
    """
    FoodLens protein component.

    Protein quality is assessed as percentage of energy
    supplied by protein rather than using an arbitrary
    gram threshold.

    Protein energy = protein(g) * 4 kcal

    Protein energy % =
        protein energy / total calories * 100
    """

    if protein is None or calories is None or calories <= 0:
        return None, "Unavailable", None

    protein_energy_percentage = (
        (protein * 4) / calories
    ) * 100

    if protein_energy_percentage < 5:
        return 2, "Low", protein_energy_percentage

    if protein_energy_percentage < 12:
        return 5, "Moderate", protein_energy_percentage

    if protein_energy_percentage < 20:
        return 8, "Good", protein_energy_percentage

    return 10, "High", protein_energy_percentage


# ============================================================
# FOODLENS SCORE
# ============================================================

def calculate_foodlens_score(nutriments):
    """
    FoodLens 1-10 nutritional scoring model.

    10 = most favourable
    1  = least favourable

    Weights:

        Sugar   = 30%
        Sodium  = 25%
        Fibre   = 20%
        Protein = 25%

    Missing nutrients are NOT treated as zero.
    Their weights are removed and the remaining
    available weights are normalized to 100%.
    """

    sugar = number_or_none(
        nutriments.get("sugars_100g")
    )

    sodium_g = number_or_none(
        nutriments.get("sodium_100g")
    )

    fibre = number_or_none(
        nutriments.get("fiber_100g")
    )

    protein = number_or_none(
        nutriments.get("proteins_100g")
    )

    calories = number_or_none(
        nutriments.get("energy-kcal_100g")
    )

    # Open Food Facts stores sodium as g/100g.
    # FoodLens standardizes sodium to mg/100g.
    sodium_mg = None

    if sodium_g is not None:
        sodium_mg = sodium_g * 1000

    sugar_component, sugar_rating = sugar_score(sugar)

    sodium_component, sodium_rating = sodium_score(
        sodium_mg
    )

    fibre_component, fibre_rating = fibre_score(
        fibre
    )

    (
        protein_component,
        protein_rating,
        protein_energy_percentage
    ) = protein_score(
        protein,
        calories
    )

    # --------------------------------------------------------
    # WEIGHTS
    # --------------------------------------------------------

    components = [
        ("sugar", sugar_component, 0.30),
        ("sodium", sodium_component, 0.25),
        ("fibre", fibre_component, 0.20),
        ("protein", protein_component, 0.25),
    ]

    weighted_total = 0.0
    available_weight = 0.0

    for name, component, weight in components:

        if component is not None:
            weighted_total += component * weight
            available_weight += weight

    # No usable nutrient data.
    if available_weight == 0:

        return {
            "score": None,
            "category": "Unavailable",
            "breakdown": {
                "sugar": {
                    "score": None,
                    "rating": "Unavailable",
                    "value": sugar
                },
                "sodium": {
                    "score": None,
                    "rating": "Unavailable",
                    "value": sodium_mg
                },
                "fibre": {
                    "score": None,
                    "rating": "Unavailable",
                    "value": fibre
                },
                "protein": {
                    "score": None,
                    "rating": "Unavailable",
                    "value": protein,
                    "energy_percentage": None
                }
            }
        }

    # Normalize available weights to 100%.
    final_score = weighted_total / available_weight

    final_score = max(
        1.0,
        min(10.0, final_score)
    )

    final_score = round(
        final_score,
        1
    )

    # --------------------------------------------------------
    # OVERALL CATEGORY
    # --------------------------------------------------------

    if final_score >= 8.5:
        category = "Excellent"

    elif final_score >= 7:
        category = "Good"

    elif final_score >= 5:
        category = "Moderate"

    elif final_score >= 3:
        category = "Poor"

    else:
        category = "Very Poor"

    return {
        "score": final_score,
        "category": category,

        "breakdown": {

            "sugar": {
                "score": sugar_component,
                "rating": sugar_rating,
                "value": clean_number(sugar),
                "unit": "g/100g"
            },

            "sodium": {
                "score": sodium_component,
                "rating": sodium_rating,
                "value": clean_number(sodium_mg),
                "unit": "mg/100g"
            },

            "fibre": {
                "score": fibre_component,
                "rating": fibre_rating,
                "value": clean_number(fibre),
                "unit": "g/100g"
            },

            "protein": {
                "score": protein_component,
                "rating": protein_rating,
                "value": clean_number(protein),
                "unit": "g/100g",
                "energy_percentage": (
                    clean_number(
                        protein_energy_percentage
                    )
                    if protein_energy_percentage is not None
                    else None
                )
            }
        }
    }


# ============================================================
# OVERALL RECOMMENDATION
# ============================================================

def overall_recommendation(
    score,
    breakdown
):
    """
    Create one simple overall recommendation.

    This is FoodLens application guidance.
    It is NOT an official WHO/FSSAI prescription.
    """

    if score is None:
        return (
            "Nutritional information is insufficient "
            "to provide a reliable FoodLens recommendation."
        )

    sugar_rating = breakdown["sugar"]["rating"]
    sodium_rating = breakdown["sodium"]["rating"]
    fibre_rating = breakdown["fibre"]["rating"]
    protein_rating = breakdown["protein"]["rating"]

    concerns = []

    if sugar_rating in ("High", "Very High"):
        concerns.append("sugar")

    if sodium_rating in ("High", "Very High"):
        concerns.append("sodium")

    if fibre_rating in ("Very Low", "Low"):
        concerns.append("fibre")

    # Overall recommendation based on score.
    if score >= 8.5:
        recommendation = (
            "Excellent nutritional profile. "
            "This can generally be included regularly "
            "as part of a balanced diet."
        )

    elif score >= 7:
        recommendation = (
            "Good nutritional profile. "
            "Generally a favourable choice."
        )

    elif score >= 5:
        recommendation = (
            "Moderate nutritional profile. "
            "Enjoy in moderation and pay attention "
            "to the highlighted nutrients."
        )

    elif score >= 3:
        recommendation = (
            "Poor nutritional profile. "
            "Best consumed occasionally."
        )

    else:
        recommendation = (
            "Very poor nutritional profile. "
            "Best limited and not relied upon regularly."
        )

    # Add the most relevant concerns.
    if concerns:

        if len(concerns) == 1:
            concern_text = concerns[0]

        elif len(concerns) == 2:
            concern_text = (
                f"{concerns[0]} and {concerns[1]}"
            )

        else:
            concern_text = ", ".join(concerns[:-1])
            concern_text += (
                f" and {concerns[-1]}"
            )

        recommendation += (
            f" Main consideration: {concern_text}."
        )

    return recommendation


# ============================================================
# CONSUMPTION FREQUENCY
# ============================================================

def consumption_guidance(score):
    """
    FoodLens frequency guidance.

    IMPORTANT:
    These are application-level general guidance categories.
    They are NOT official WHO/FSSAI recommended frequencies.

    Frequency vocabulary intentionally uses:
        Once
        Twice
        Multiple times

    combined with:
        Daily
        Weekly
        Monthly
    """

    if score is None:

        return {
            "frequency": "Unavailable",
            "period": "Unavailable",
            "display": "Unavailable",
            "reason": (
                "There is not enough nutritional data "
                "to provide frequency guidance."
            )
        }

    if score >= 8.5:

        return {
            "frequency": "Multiple times",
            "period": "Daily",
            "display": "Multiple Times Daily",
            "reason": (
                "The product has a comparatively favourable "
                "nutritional profile."
            )
        }

    if score >= 7:

        return {
            "frequency": "Once",
            "period": "Daily",
            "display": "Once Daily",
            "reason": (
                "The product has a generally favourable "
                "nutritional profile."
            )
        }

    if score >= 5:

        return {
            "frequency": "Twice",
            "period": "Weekly",
            "display": "Twice Weekly",
            "reason": (
                "The product has a moderate nutritional "
                "profile and is better consumed in moderation."
            )
        }

    if score >= 3:

        return {
            "frequency": "Once",
            "period": "Weekly",
            "display": "Once Weekly",
            "reason": (
                "The product has a poorer nutritional profile "
                "and is better treated as an occasional food."
            )
        }

    return {
        "frequency": "Once",
        "period": "Monthly",
        "display": "Once Monthly",
        "reason": (
            "The product has a very poor nutritional profile "
            "and should be limited."
        )
    }


# ============================================================
# PRODUCT DATA EXTRACTION
# ============================================================

def build_product_response(product, barcode=None):

    nutriments = product.get("nutriments") or {}

    # --------------------------------------------------------
    # BASIC PRODUCT INFORMATION
    # --------------------------------------------------------

    product_name = (
        product.get("product_name")
        or product.get("product_name_en")
        or product.get("generic_name")
        or "Unknown Product"
    )

    brand = (
        product.get("brands")
        or "Unknown Brand"
    )

    image = (
        product.get("image_front_url")
        or product.get("image_url")
        or ""
    )

    ingredients = (
        product.get("ingredients_text")
        or product.get("ingredients_text_en")
        or ""
    )

    # --------------------------------------------------------
    # NUTRITION PER 100g
    # --------------------------------------------------------

    calories = number_or_none(
        nutriments.get("energy-kcal_100g")
    )

    carbohydrates = number_or_none(
        nutriments.get("carbohydrates_100g")
    )

    protein = number_or_none(
        nutriments.get("proteins_100g")
    )

    total_fat = number_or_none(
        nutriments.get("fat_100g")
    )

    saturated_fat = number_or_none(
        nutriments.get("saturated-fat_100g")
    )

    sugar = number_or_none(
        nutriments.get("sugars_100g")
    )

    fibre = number_or_none(
        nutriments.get("fiber_100g")
    )

    sodium_g = number_or_none(
        nutriments.get("sodium_100g")
    )

    sodium_mg = None

    if sodium_g is not None:
        sodium_mg = sodium_g * 1000

    cholesterol = number_or_none(
        nutriments.get("cholesterol_100g")
    )

    calcium = number_or_none(
        nutriments.get("calcium_100g")
    )

    iron = number_or_none(
        nutriments.get("iron_100g")
    )

    trans_fat = number_or_none(
        nutriments.get("trans-fat_100g")
    )

    # --------------------------------------------------------
    # SERVING SIZE
    # --------------------------------------------------------

    serving_size = (
        product.get("serving_size")
        or ""
    )

    serving_quantity = number_or_none(
        product.get("serving_quantity")
    )

    serving_unit = (
        product.get("serving_quantity_unit")
        or ""
    ).strip().lower()

    serving_value_g = None

    if serving_quantity is not None:

        if serving_unit == "g":
            serving_value_g = serving_quantity

        elif serving_unit == "kg":
            serving_value_g = serving_quantity * 1000

    # We do not invent a serving size if OFF doesn't provide one.
    if not serving_size:
        serving_display = "Unavailable"
        serving_available = False

    else:
        serving_display = serving_size
        serving_available = True

    # --------------------------------------------------------
    # FOODLENS SCORE
    # --------------------------------------------------------

    foodlens = calculate_foodlens_score(
        nutriments
    )

    score = foodlens["score"]

    recommendation = overall_recommendation(
        score,
        foodlens["breakdown"]
    )

    frequency = consumption_guidance(
        score
    )

    # --------------------------------------------------------
    # FINAL RESPONSE
    # --------------------------------------------------------

    return {

        "barcode": (
            barcode
            or product.get("code")
        ),

        "name": product_name,

        "brand": brand,

        "image": image,

        "ingredients": ingredients,

        # ----------------------------------------------------
        # FOODLENS
        # ----------------------------------------------------

        "foodlens": {

            "score": score,

            "category": foodlens["category"],

            "breakdown": foodlens["breakdown"],

            "recommendation": recommendation,

            "method": (
                "FoodLens 1-10 hybrid nutrient model "
                "informed by WHO guidance and "
                "international nutrient-profile thresholds."
            )
        },

        # Keep this structure for compatibility with
        # your earlier frontend/backend versions.
        "health_assessment": {

            "category": foodlens["category"],

            "score": score,

            "method": (
                "FoodLens 1-10 hybrid nutrient model"
            )
        },

        # ----------------------------------------------------
        # CONSUMPTION
        # ----------------------------------------------------

        "consumption_guidance": frequency,

        # ----------------------------------------------------
        # NUTRITION
        # ----------------------------------------------------

        "nutrition": {

            "calories_kcal_100g":
                clean_number(calories),

            "carbohydrates_g_100g":
                clean_number(carbohydrates),

            "protein_g_100g":
                clean_number(protein),

            "total_fat_g_100g":
                clean_number(total_fat),

            "saturated_fat_g_100g":
                clean_number(saturated_fat),

            "sugar_g_100g":
                clean_number(sugar),

            "dietary_fiber_g_100g":
                clean_number(fibre),

            "sodium_mg_100g":
                clean_number(sodium_mg),

            "cholesterol_mg_100g":
                clean_number(cholesterol),

            "calcium_mg_100g":
                clean_number(calcium),

            "iron_mg_100g":
                clean_number(iron),

            "trans_fat_g_100g":
                clean_number(trans_fat)
        },

        # ----------------------------------------------------
        # SERVING SIZE
        # ----------------------------------------------------

        "serving_size": {

            "available": serving_available,

            "display": serving_display,

            "value_g": (
                clean_number(serving_value_g)
                if serving_value_g is not None
                else None
            ),

            "unit": (
                serving_unit
                if serving_unit
                else None
            )
        },

        # ----------------------------------------------------
        # IMPORTANT:
        # Open Food Facts Nutri-Score is NOT used by
        # FoodLens scoring.
        #
        # We keep it only as source metadata so that
        # the frontend can ignore it.
        # ----------------------------------------------------

        "open_food_facts": {

            "nutriscore": None,

            "data_source":
                "Open Food Facts",

            "nutrition_data_per":
                product.get("nutrition_data_per")
        }
    }


# ============================================================
# HOME PAGE
# ============================================================

@app.route("/")
def home():

    return render_template(
        "index.html"
    )


# ============================================================
# MAIN FOOD DATA ENDPOINT
# ============================================================

@app.route("/get_food_data")
def get_food_data():

    query = request.args.get(
        "query",
        ""
    ).strip()

    if not query:

        return jsonify({
            "error":
                "Please provide a product name or barcode."
        }), 400

    # ========================================================
    # PATH A — BARCODE
    # ========================================================

    if query.isdigit():

        # IMPORTANT:
        # Keep barcode as STRING.
        # Do not convert to integer because
        # leading zeros can matter.

        barcode = query

        encoded_barcode = urllib.parse.quote(
            barcode,
            safe=""
        )

        url = (
            f"https://{OFF_HOST}/api/v2/product/"
            f"{encoded_barcode}.json"
        )

        params = {

            "fields": (
                "code,"
                "product_name,"
                "product_name_en,"
                "generic_name,"
                "brands,"
                "image_front_url,"
                "image_url,"
                "ingredients_text,"
                "ingredients_text_en,"
                "serving_size,"
                "serving_quantity,"
                "serving_quantity_unit,"
                "nutrition_data_per,"
                "nutriments"
            )
        }

        data, status = off_get(
            url,
            params=params
        )

        # Genuine product not found.
        if status == 404:

            return jsonify({

                "error":
                    "Product not found in Open Food Facts.",

                "barcode": barcode

            }), 404

        # Temporary error.
        if data is None:

            return jsonify({

                "error": (
                    "OpenFoodFacts is temporarily "
                    "unavailable. Please try again shortly."
                ),

                "details": str(status),

                "barcode": barcode

            }), 503

        # Open Food Facts product status.
        if (
            data.get("status") != 1
            or "product" not in data
        ):

            return jsonify({

                "error": (
                    "Barcode was not found in the "
                    "Open Food Facts database."
                ),

                "barcode": barcode

            }), 404

        product = data["product"]

        return jsonify(
            build_product_response(
                product,
                barcode=barcode
            )
        )


    # ========================================================
    # PATH B — TEXT SEARCH
    # ========================================================

    url = (
        f"https://{OFF_HOST}/api/v2/search"
    )

    params = {

        "search_terms": query,

        "fields": (
            "code,"
            "product_name,"
            "product_name_en,"
            "generic_name,"
            "brands,"
            "image_front_url,"
            "image_url,"
            "ingredients_text,"
            "ingredients_text_en,"
            "serving_size,"
            "serving_quantity,"
            "serving_quantity_unit,"
            "nutrition_data_per,"
            "nutriments"
        ),

        "page_size": 1
    }

    data, status = off_get(
        url,
        params=params
    )

    if data is None:

        return jsonify({

            "error": (
                "OpenFoodFacts search is temporarily "
                "unavailable. Please try again shortly."
            ),

            "details": str(status)

        }), 503

    products = data.get(
        "products",
        []
    )

    if not products:

        return jsonify({

            "error":
                "No matching food product found.",

            "query": query

        }), 404

    product = products[0]

    return jsonify(
        build_product_response(
            product,
            barcode=product.get("code")
        )
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():

    return jsonify({

        "status":
            "FoodLens backend is running",

        "open_food_facts":
            OFF_HOST,

        "foodlens_version":
            "1.0",

        "scoring":
            "Sugar 30% | Sodium 25% | Fibre 20% | Protein 25%"
    })


# ============================================================
# RUN SERVER
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5003,
        debug=True
    )