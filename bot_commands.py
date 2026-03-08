@bot.command(name='genres')
async def genres(ctx):
    genres_list = ["1. Post-Apocalyptic", "2. Sci-Fi", "3. Survival", "4. Adventure"]
    response = "Available genres:\n" + "\n".join(genres_list)
    await ctx.send(response)

@bot.command(name='start')
async def start(ctx, *, genre=None):
    if genre is None:
        genre = "Post-Apocalyptic"
    elif genre.isdigit() and 1 <= int(genre) <= 4:
        genre_map = {1: "Post-Apocalyptic", 2: "Sci-Fi", 3: "Survival", 4: "Adventure"}
        genre = genre_map[int(genre)]
    # Logic to start game with the selected genre
    await ctx.send(f"Starting game in {genre} genre!")
