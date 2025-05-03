import requests

def get_all_symbols_from_kucoin():
    try:
        url = "https://api.kucoin.com/api/v1/contracts/active"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        return [x["symbol"] for x in data["data"]]
    except Exception as e:
        print(f"Erreur récupération contrats PERP KuCoin: {e}")
        return []
