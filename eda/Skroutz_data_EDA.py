import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import re


data = pd.read_csv(
    r'C:\Users\StavrosKV\Documents\Projects\ProjectsPY\Clean\Phones_skroutz_clean\clean_2025-06-23.csv')

data.columns  # Display the columns of the DataFrame
data.shape  # Display the shape of the DataFrame (rows, columns)
data.info()

# display a histogram of the Price_EUR column
plt.figure(figsize=(10, 6))
sns.histplot(data['Price_EUR'], bins=30, kde=True)
plt.title('Distribution of Phone Prices in EUR')
plt.xlabel('Price in EUR')
plt.ylabel('Frequency')
plt.show()
# display a bar plot of the number of phones by brand
plt.figure(figsize=(12, 6))
sns.countplot(data=data, x='Brand',
              order=data['Brand'].value_counts().head(12).index)
plt.title('Number of Phones by Brand')
plt.xlabel('Brand')
plt.ylabel('Number of Phones')
plt.xticks(rotation=45)
plt.show()

# display average price of phones by brand
plt.figure(figsize=(12, 6))
sns.barplot(data=data, x='Brand', y='Price_EUR', estimator=np.mean,
            order=data['Brand'].value_counts().head(12).index)
plt.title('Average Price of Phones by Brand')
plt.xlabel('Brand')
plt.ylabel('Average Price in EUR')
plt.xticks(rotation=45)
plt.show()
