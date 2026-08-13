from google.colab import drive
drive.mount('/content/drive')
df_ads = pd.read_csv('/content/drive/My Drive/Data_Shopping/export_google_ads.csv')
df_feed = pd.read_xml('/content/drive/My Drive/Data_Shopping/feed_shopping_daily.xml')
