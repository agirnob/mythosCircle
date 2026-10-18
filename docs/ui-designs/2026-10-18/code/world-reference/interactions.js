
    // Simple client-side search filtering
    const searchInput = document.querySelector('.mc-directory-search input');
    const sections = document.querySelectorAll('.mc-overview-main > section');
    
    searchInput.addEventListener('input', (e) => {
      const term = e.target.value.toLowerCase();
      
      sections.forEach(section => {
        const cards = section.querySelectorAll('.mc-entity-card');
        let sectionHasMatches = false;
        
        cards.forEach(card => {
          const name = card.querySelector('.mc-entity-name').textContent.toLowerCase();
          const meta = card.querySelector('.mc-entity-meta').textContent.toLowerCase();
          const desc = card.querySelector('.mc-entity-description').textContent.toLowerCase();
          
          if (name.includes(term) || meta.includes(term) || desc.includes(term)) {
            card.style.display = 'flex';
            sectionHasMatches = true;
          } else {
            card.style.display = 'none';
          }
        });
        
        section.style.display = sectionHasMatches ? 'block' : 'none';
      });
    });
  
